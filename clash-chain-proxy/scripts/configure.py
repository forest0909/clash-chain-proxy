#!/usr/bin/env python3
"""Clash Verge Claude 链式代理配置；默认只预览。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[1]
MARKER = '\n// BEGIN clash-claude-chain managed wrapper\n'
WRAPPER = MARKER + '''const __claudeChainPrevious = main;
main = function(config, profileName) {
  config = __claudeChainPrevious(config, profileName);
  const groups = config["proxy-groups"] || [];
  for (const group of groups) {
    if (!Array.isArray(group.proxies) || group.name === "MyIP") continue;
    group.proxies = group.proxies.filter(p => p !== "MyAI" && p !== "MyIP");
    if (group.proxies.length === 0 && !(group.use || []).length) group.proxies.push("DIRECT");
  }
  return config;
};
'''


class ConfigError(Exception):
    pass


def load(path):
    return yaml.safe_load(path.read_text(encoding='utf-8')) or {}


def detect_dir():
    app = 'io.github.clash-verge-rev.clash-verge-rev'
    candidates = [Path.home() / 'Library/Application Support' / app,
                  Path(os.environ.get('APPDATA', str(Path.home() / 'AppData/Roaming'))) / app,
                  Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / app]
    found = [p for p in candidates if (p / 'profiles.yaml').is_file()]
    if len(found) != 1:
        raise ConfigError('未找到唯一配置目录，请用 --dir 指定包含 profiles.yaml 的目录。')
    return found[0]


def ext_path(directory, uid, suffix):
    base = (directory / 'profiles').resolve()
    path = (base / (str(uid) + suffix)).resolve()
    if path.parent != base:
        raise ConfigError('扩展文件路径不在 profiles 目录内。')
    return path


def merge_named(doc, node):
    for area in ('prepend', 'append'):
        doc[area] = [n for n in doc.get(area, []) or [] if n.get('name') != node['name']]
    if node['name'] in (doc.get('delete') or []):
        raise ConfigError('删除列表与 MyAI/MyIP 名称冲突，请先确认应保留的配置。')
    doc['prepend'].insert(0, node)


def configured_subscriptions(directory, subs, current):
    """Inspect extensions locally; return identifiers and status, never credentials."""
    candidates, skipped = [], []
    for sub in subs:
        summary = {'uid': sub['uid'], 'name': sub['name'], 'current': sub['uid'] == current}
        try:
            option = sub.get('option') or {}
            if any(not option.get(k) for k in ('proxies', 'groups', 'rules', 'script')):
                continue
            paths = {k: ext_path(directory, option[k], '.js' if k == 'script' else '.yaml')
                     for k in ('proxies', 'groups', 'rules', 'script')}
            if any(not path.is_file() for path in paths.values()):
                skipped.append(dict(summary, reason='扩展文件缺失，未能完整检测'))
                continue
            docs = {k: load(paths[k]) for k in ('proxies', 'groups', 'rules')}
            nodes = [n for area in ('prepend', 'append') for n in docs['proxies'].get(area, []) or []
                     if n.get('name') == 'MyAI']
            groups = [n for area in ('prepend', 'append') for n in docs['groups'].get(area, []) or []
                      if n.get('name') == 'MyIP']
            if not nodes and not groups:
                continue
            if (len(nodes) != 1 or len(groups) != 1 or nodes[0].get('type') != 'socks5'
                    or not nodes[0].get('dialer-proxy')
                    or nodes[0]['dialer-proxy'] in ('MyAI', 'MyIP')
                    or groups[0].get('type') != 'select'
                    or 'MyAI' not in (groups[0].get('proxies') or [])
                    or 'MyAI' in (docs['proxies'].get('delete') or [])
                    or 'MyIP' in (docs['groups'].get('delete') or [])):
                skipped.append(dict(summary, reason='链式代理对象不完整或存在冲突'))
                continue
            # Detection is not authorization to adopt an unrelated same-name object.
            state = directory / '.claude-chain-backups'
            managed = any(json.loads(p.read_text()).get('uid') == sub['uid']
                          for p in state.glob('*/manifest.json'))
            candidates.append(dict(summary, managed=managed))
        except (OSError, yaml.YAMLError, ValueError, KeyError, TypeError, AttributeError):
            skipped.append(dict(summary, reason='配置无法读取或结构无效'))
    return {'total': len(subs), 'subscriptions': candidates, 'skipped': skipped}


def plan(directory, sub, config, prepare, adopt):
    option = sub.get('option') or {}
    if any(not option.get(k) for k in ('proxies', 'groups', 'rules', 'script')):
        raise ConfigError('缺少扩展关联，请先为该订阅打开节点、代理组、规则和脚本编辑器并保存。')
    paths = {k: ext_path(directory, option[k], '.js' if k == 'script' else '.yaml')
             for k in ('proxies', 'groups', 'rules', 'script')}
    subpath = ext_path(directory, sub['file'], '')
    source = load(subpath)
    groups = source.get('proxy-groups') or []
    if not groups or groups[0].get('name') in (None, 'MyAI', 'MyIP'):
        raise ConfigError('订阅首个代理组缺失或与保留名称冲突。')
    if any(n.get('name') in ('MyAI', 'MyIP') for n in
           (source.get('proxies') or []) + groups):
        raise ConfigError('原订阅中存在 MyAI/MyIP 同名对象，请先处理名称冲突。')
    upstream = groups[0]['name']
    docs = {k: load(paths[k]) if paths[k].exists() else {} for k in ('proxies', 'groups', 'rules')}
    old_nodes = [n for a in ('prepend', 'append') for n in docs['proxies'].get(a, []) or []
                 if n.get('name') == 'MyAI']
    if len(old_nodes) > 1:
        raise ConfigError('已有多个 MyAI 节点，需先确认保留哪个。')
    old = old_nodes[0] if old_nodes else {}
    old_groups = [n for a in ('prepend', 'append') for n in docs['groups'].get(a, []) or []
                  if n.get('name') == 'MyIP']
    state = directory / '.claude-chain-backups'
    managed = any(json.loads(p.read_text()).get('uid') == sub['uid']
                  for p in state.glob('*/manifest.json')) if state.exists() else False
    if (old or old_groups) and not managed and not adopt:
        raise ConfigError('已有 MyAI/MyIP 对象。先确认其用途，获准接管后使用 --adopt-existing。')
    if old and old.get('type') != 'socks5':
        raise ConfigError('已有 MyAI 节点不是 socks5，不能接管。')
    node = dict(old)
    node.update(type='socks5', name='MyAI', **{'dialer-proxy': upstream})
    connection = config.get('connection') or {}
    for key in ('server', 'port', 'username', 'password'):
        value = connection.get(key)
        if value not in (None, ''):
            node[key] = value
        else:
            node.setdefault(key, None if key == 'port' else '')
    complete = bool(node['server']) and isinstance(node['port'], int) and 1 <= node['port'] <= 65535
    if not complete and not prepare:
        raise ConfigError('缺少有效地址或端口。可自行填写；首次生成待填配置需使用 --prepare。')
    merge_named(docs['proxies'], node)
    group = dict(old_groups[0]) if old_groups else {'type': 'select', 'name': 'MyIP'}
    if group.get('type') != 'select':
        raise ConfigError('已有 MyIP 不是 select 组，不能接管。')
    group['proxies'] = list(dict.fromkeys(['MyAI', upstream] + (group.get('proxies') or [])))
    merge_named(docs['groups'], group)
    custom = config.get('custom_rules') or []
    allowed = {'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'IP-CIDR', 'IP-CIDR6', 'PROCESS-NAME'}
    for rule in custom:
        parts = rule.split(',') if isinstance(rule, str) else []
        if len(parts) not in (3, 4) or parts[0] not in allowed or not parts[1] or parts[2] != 'MyIP' or (len(parts) == 4 and (parts[0] not in {'IP-CIDR','IP-CIDR6'} or parts[3] != 'no-resolve')):
            raise ConfigError('自定义规则格式无效；需为受支持的匹配类型、匹配值、MyIP（IP 可加 no-resolve）。')
    rules = docs['rules']
    existing = rules.get('prepend') or []
    defaults = load(ROOT / 'assets/claude-rules.yaml')
    rules['prepend'] = list(dict.fromkeys(custom + defaults + existing))
    script = paths['script'].read_text(encoding='utf-8') if paths['script'].exists() else ''
    if MARKER in script:
        script = script.split(MARKER)[0]
    if not script.strip():
        script = 'function main(config, profileName) { return config; }\n'
    result = {paths[k]: yaml.safe_dump(v, allow_unicode=True, sort_keys=False).encode() for k, v in docs.items()}
    result[paths['script']] = (script + WRAPPER).encode()
    print('目标订阅：' + sub['name'])
    print('上游代理组：' + upstream)
    print('配置：MyAI → SOCKS5；MyIP 可选择 MyAI 或上游代理组。')
    print('连接状态：' + ('地址和端口已填写（尚未验证连通性）' if complete else '等待客户在界面填写，暂勿激活'))
    print(f'规则：{len(defaults)} 条内置，{len(custom)} 条用户追加；保留已有规则。')
    print('YAML 数据保留，注释和排版会重新生成；原脚本保留，并追加防循环处理。')
    return result


def apply(directory, result, uid):
    changes = {p: data for p, data in result.items() if not p.exists() or p.read_bytes() != data}
    if not changes:
        print('配置一致，无需写入。')
        return
    backup = directory / '.claude-chain-backups' / uuid.uuid4().hex
    backup.mkdir(parents=True, mode=0o700)
    manifest = {'uid': uid, 'files': []}
    for i, (path, data) in enumerate(changes.items()):
        old = path.read_bytes() if path.exists() else None
        if old is not None:
            saved = backup / str(i)
            saved.touch(mode=0o600)
            saved.write_bytes(old)
        manifest['files'].append({'name': path.name, 'backup': str(i) if old is not None else None,
                                  'after': hashlib.sha256(data).hexdigest()})
    record = backup / 'manifest.json'
    record.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    try:
        for path, data in changes.items():
            path.touch(mode=0o600, exist_ok=True)
            path.write_bytes(data)
    except OSError:
        for entry in manifest['files']:
            path = ext_path(directory, entry['name'], '')
            if entry['backup'] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes((backup / entry['backup']).read_bytes())
        raise
    print(f'已写入 {len(changes)} 个文件。备份：{backup.name}')
    print('填写完成后，请重新激活订阅，并在代理页选择 MyIP → MyAI。')


def restore(directory, snapshot, commit):
    if Path(snapshot).name != snapshot:
        raise ConfigError('请提供备份编号。')
    backup = directory / '.claude-chain-backups' / snapshot
    manifest = json.loads((backup / 'manifest.json').read_text())
    for entry in manifest['files']:
        path = ext_path(directory, entry['name'], '')
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['after']:
            raise ConfigError('配置在备份后发生变化，停止还原；请人工比较，避免覆盖后续修改。')
    print(f'将还原 {len(manifest["files"])} 个扩展文件。')
    if commit:
        for entry in manifest['files']:
            path = ext_path(directory, entry['name'], '')
            if entry['backup'] is None:
                path.unlink()
            else:
                path.write_bytes((backup / entry['backup']).read_bytes())
        print('已还原，请重新激活订阅。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', type=Path)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--list-configured', action='store_true', help='只读扫描，列出已有 MyAI/MyIP 链式代理的订阅')
    parser.add_argument('--subscription', help='订阅 UID 或完整名称；不接受易变的编号')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--prepare', action='store_true', help='允许生成待客户自行填写的空连接配置')
    parser.add_argument('--adopt-existing', action='store_true', help='已确认接管现有 MyAI/MyIP 对象')
    parser.add_argument('--apply', action='store_true', help='确认后写入；默认只预览')
    parser.add_argument('--restore', help='备份编号；配合 --apply 才还原')
    args = parser.parse_args()
    try:
        if args.list_configured and (args.apply or args.restore or args.list):
            raise ConfigError('--list-configured 不能与 --apply、--restore 或 --list 同用。')
        directory = args.dir.expanduser().resolve() if args.dir else detect_dir()
        if args.restore:
            restore(directory, args.restore, args.apply)
            return
        subs = [s for s in load(directory / 'profiles.yaml').get('items', [])
                if s.get('type') in ('remote', 'local') and s.get('name')]
        if args.list_configured:
            current = load(directory / 'profiles.yaml').get('current')
            print(json.dumps(configured_subscriptions(directory, subs, current), ensure_ascii=False))
            return
        if args.list:
            current = load(directory / 'profiles.yaml').get('current')
            for s in subs:
                print(json.dumps({'uid': s['uid'], 'name': s['name'], 'current': s['uid'] == current}, ensure_ascii=False))
            return
        selected = [s for s in subs if args.subscription in (s['uid'], s['name'])] if args.subscription else []
        if len(selected) != 1:
            raise ConfigError('请先 --list，再用 --subscription 指定唯一订阅名称或 UID。')
        result = plan(directory, selected[0], load(args.config) if args.config else {}, args.prepare, args.adopt_existing)
        if args.apply:
            apply(directory, result, selected[0]['uid'])
        else:
            print('仅预览，未写入。')
    except ConfigError as exc:
        print('操作未完成：' + str(exc), file=sys.stderr)
        return 1
    except (OSError, yaml.YAMLError, ValueError, KeyError, TypeError, AttributeError):
        # 不输出 YAML 解析上下文或文件内容，避免暴露凭证。
        print('操作未完成：请检查配置结构、订阅关联、名称冲突、地址/端口及文件权限；未自动重试。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
