#!/usr/bin/env python3
"""
proxy_cached.py

Minimal pub.dev proxy that:
 - Always fetches package metadata live from pub.dev (no long-term metadata cache)
 - Rewrites archive_url entries to point at this proxy
 - Caches tarballs on first download (by package/version) and serves cached archives
 - Provides admin endpoints to purge or prefetch cached archives

Usage:
  python3 proxy_cached.py --host 0.0.0.0 --port 8080 --cache-dir /srv/pub/packages

Requirements: Flask, requests
"""

import argparse
import os
import requests
import shutil
from flask import Flask, jsonify, send_file, abort, request, Response, url_for, stream_with_context

REAL_PUB = "https://pub.dev"
app = Flask(__name__)

def version_dir(cache_dir, name, version):
    return os.path.join(cache_dir, name, version)

def cached_tar_path(cache_dir, name, version):
    vd = version_dir(cache_dir, name, version)
    if not os.path.isdir(vd):
        return None
    for f in os.listdir(vd):
        if f.endswith('.tar.gz'):
            return os.path.join(vd, f)
    return None

def fetch_upstream(path, stream=False, params=None, headers=None, method='get', data=None):
    url = REAL_PUB.rstrip('/') + path
    try:
        resp = requests.request(method, url, params=params, headers=headers, data=data, stream=stream, timeout=30)
        return resp
    except requests.RequestException as e:
        app.logger.error('Upstream fetch failed %s %s', url, e)
        return None

@app.route('/api/packages/<name>')
def api_package(name):
    """Always fetch metadata from upstream and rewrite archive_url to local when cached."""
    cache_dir = app.config['CACHE_DIR']
    r = fetch_upstream(f"/api/packages/{name}")
    if r is None:
        return abort(502)
    if r.status_code != 200:
        # pass through errors
        return Response(r.content, status=r.status_code, headers=r.headers.items())

    data = r.json()
    # rewrite archive urls to our proxy; if tar cached use local URL, otherwise still point to proxy (so proxy will fetch & cache on demand)
    for v in data.get('versions', []):
        ver = v.get('version')
        if not ver:
            continue
        # point archive_url to our proxy endpoint for this version
        v['archive_url'] = url_for('package_archive', name=name, version=ver, _external=True)
        # if we have a cached tar, ensure it will be used (archive_url already points to us)
    return jsonify(data)

@app.route('/api/packages/<name>/versions/<version>.json')
def api_package_version(name, version):
    cache_dir = app.config['CACHE_DIR']
    # fetch upstream metadata for this exact version
    r = fetch_upstream(f"/api/packages/{name}/versions/{version}.json")
    if r is None:
        return abort(502)
    if r.status_code != 200:
        return Response(r.content, status=r.status_code, headers=r.headers.items())
    data = r.json()
    # ensure archive_url points to us (so client will request our archive endpoint)
    data['archive_url'] = url_for('package_archive', name=name, version=version, _external=True)
    return jsonify(data)

@app.route('/packages/<name>/versions/<version>.tar.gz')
def package_archive(name, version):
    """Serve cached tarball if present; otherwise stream from upstream while saving to cache."""
    cache_dir = app.config['CACHE_DIR']
    tar = cached_tar_path(cache_dir, name, version)
    if tar:
        app.logger.info('Serving cached %s %s', name, version)
        return send_file(tar, as_attachment=True)

    # not cached: fetch from upstream and write to disk while streaming to client
    upstream_path = f"/packages/{name}/versions/{version}.tar.gz"
    r = fetch_upstream(upstream_path, stream=True)
    if r is None:
        return abort(502)
    if r.status_code != 200:
        return Response(r.content, status=r.status_code, headers=r.headers.items())

    vd = version_dir(cache_dir, name, version)
    os.makedirs(vd, exist_ok=True)
    filename = os.path.join(vd, f"{name}-{version}.tar.gz")
    tmpname = filename + '.part'

    try:
        with open(tmpname, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
                    fh.flush()
        # move into place atomically
        os.replace(tmpname, filename)
        app.logger.info('Cached %s %s -> %s', name, version, filename)
    except Exception as e:
        app.logger.error('Failed to cache %s %s: %s', name, version, e)
        # clean up partial
        if os.path.exists(tmpname):
            try:
                os.remove(tmpname)
            except Exception:
                pass
        # stream upstream content directly to client
        return Response(stream_with_context(r.iter_content(chunk_size=8192)), content_type=r.headers.get('content-type', 'application/octet-stream'))

    # serve the newly cached file
    return send_file(filename, as_attachment=True)

@app.route('/admin/purge/<name>', methods=['POST', 'GET'])
@app.route('/admin/purge/<name>/<version>', methods=['POST', 'GET'])
def admin_purge(name, version=None):
    """Purge a package or a specific version from cache. Use POST for safety, GET allowed for convenience."""
    cache_dir = app.config['CACHE_DIR']
    pkg_dir = os.path.join(cache_dir, name)
    if version:
        ver_dir = os.path.join(pkg_dir, version)
        if os.path.isdir(ver_dir):
            shutil.rmtree(ver_dir)
            return jsonify({'status': 'purged', 'package': name, 'version': version})
        return jsonify({'status': 'not_found', 'package': name, 'version': version}), 404
    else:
        if os.path.isdir(pkg_dir):
            shutil.rmtree(pkg_dir)
            return jsonify({'status': 'purged', 'package': name})
        return jsonify({'status': 'not_found', 'package': name}), 404

@app.route('/admin/prefetch/<name>/<version>', methods=['POST'])
def admin_prefetch(name, version):
    """Fetch and cache a specific package version now (useful to pre-warm)."""
    cache_dir = app.config['CACHE_DIR']
    # reuse package_archive logic by invoking upstream fetch and saving
    upstream_path = f"/packages/{name}/versions/{version}.tar.gz"
    r = fetch_upstream(upstream_path, stream=True)
    if r is None:
        return abort(502)
    if r.status_code != 200:
        return Response(r.content, status=r.status_code, headers=r.headers.items())

    vd = version_dir(cache_dir, name, version)
    os.makedirs(vd, exist_ok=True)
    filename = os.path.join(vd, f"{name}-{version}.tar.gz")
    tmpname = filename + '.part'
    try:
        with open(tmpname, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
                    fh.flush()
        os.replace(tmpname, filename)
        return jsonify({'status': 'cached', 'package': name, 'version': version, 'path': filename})
    except Exception as e:
        if os.path.exists(tmpname):
            try:
                os.remove(tmpname)
            except Exception:
                pass
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def proxy_fallback(path):
    """Proxy everything else to upstream pub.dev preserving method and body minimally."""
    upstream = REAL_PUB.rstrip('/') + '/' + path
    headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
    try:
        resp = requests.request(request.method, upstream, headers=headers, params=request.args, data=request.get_data(), stream=True, timeout=30)
    except requests.RequestException as e:
        app.logger.error('Fallback proxy error %s', e)
        return abort(502)
    excluded = ['content-encoding', 'transfer-encoding', 'connection']
    headers_out = [(name, value) for (name, value) in resp.raw.headers.items() if name.lower() not in excluded]
    return Response(stream_with_context(resp.iter_content(chunk_size=8192)), status=resp.status_code, headers=headers_out)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', default=8080, type=int)
    p.add_argument('--cache-dir', default='./packages')
    p.add_argument('--upstream', default=REAL_PUB)
    args = p.parse_args()

    os.makedirs(args.cache_dir, exist_ok=True)
    app.config['CACHE_DIR'] = os.path.abspath(args.cache_dir)
    REAL_PUB = args.upstream.rstrip('/')
    print(f"Starting pub mirror proxy on http://{args.host}:{args.port} with cache {app.config['CACHE_DIR']} and upstream {REAL_PUB}")
    app.run(host=args.host, port=args.port, threaded=True)
