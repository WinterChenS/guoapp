"""列出应用内全部站源，用于核对 --all-sources 的实际范围。

数据来源是 lib/models.dart 里的 SourceSite 定义、primaryValues / restrictedValues
与 values。任何一步解析失败都直接报错退出，不输出空清单或残缺清单。
"""

import argparse
import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
models_path = root / 'lib' / 'models.dart'


def match_pair(text, start, opener, closer):
    depth = 0
    for index in range(start, len(text)):
        character = text[index]
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError('缺少与 %s 配对的 %s' % (opener, closer))


def literal_arguments(text, paren):
    end = match_pair(text, paren, '(', ')')
    return text[paren + 1:end], end


def parse_source_site(arguments):
    values = re.findall(r"'([^']*)'", arguments)
    if len(values) < 2:
        raise ValueError('SourceSite 参数不足：%s' % arguments.strip())
    return values[0], values[1], values[2] if len(values) > 2 else ''


def parse_definitions(text):
    definitions = {}
    sites = {}
    pattern = re.compile(r'static const (\w+)\s*=\s*SourceSite\(')
    for match in pattern.finditer(text):
        arguments, _ = literal_arguments(text, match.end() - 1)
        identifier, name, description = parse_source_site(arguments)
        definitions[match.group(1)] = identifier
        sites[identifier] = {'name': name, 'description': description}
    if not definitions:
        raise ValueError('lib/models.dart 中找不到任何 SourceSite 定义')
    return definitions, sites


def parse_list_body(text, name):
    match = re.search(r'static const ' + name + r'\s*=\s*\[', text)
    if not match:
        raise ValueError('lib/models.dart 中找不到 %s 定义' % name)
    start = text.index('[', match.end() - 1)
    end = match_pair(text, start, '[', ']')
    return text[start + 1:end]


def parse_items(body):
    items = []
    index = 0
    while index < len(body):
        character = body[index]
        if body.startswith('SourceSite', index):
            paren = body.index('(', index)
            arguments, end = literal_arguments(body, paren)
            items.append(('literal', arguments))
            index = end + 1
        elif character.isalpha() or character == '_':
            word = re.match(r'\w+', body[index:]).group(0)
            items.append(('reference', word))
            index += len(word)
        else:
            index += 1
    return items


def resolve_items(items, definitions, sites):
    identifiers = []
    for kind, value in items:
        if kind == 'literal':
            identifier, name, description = parse_source_site(value)
            sites.setdefault(identifier, {'name': name, 'description': description})
        else:
            identifier = definitions.get(value)
            if identifier is None:
                raise ValueError('未知的站源引用：%s' % value)
        identifiers.append(identifier)
    return identifiers


def parse_default_sources(text, definitions):
    match = re.search(
        r'static const values\s*=\s*allSourcesEnabled\s*\?\s*[\w.]+\s*:\s*\[([^\]]*)\]\s*;',
        text)
    if not match:
        raise ValueError('lib/models.dart 中找不到 values 定义')
    identifiers = []
    for word in re.findall(r'\w+', match.group(1)):
        identifier = definitions.get(word)
        if identifier is None:
            raise ValueError('values 默认分支中未知的站源引用：%s' % word)
        identifiers.append(identifier)
    if not identifiers:
        raise ValueError('values 的默认分支为空')
    return identifiers


def collect(text):
    definitions, sites = parse_definitions(text)
    primary = resolve_items(
        parse_items(parse_list_body(text, 'primaryValues')), definitions, sites)
    restricted = resolve_items(
        parse_items(parse_list_body(text, 'restrictedValues')), definitions, sites)
    default = parse_default_sources(text, definitions)
    ordered = primary + restricted
    duplicates = sorted({item for item in ordered if ordered.count(item) > 1})
    if duplicates:
        raise ValueError('站源 ID 重复：%s' % '、'.join(duplicates))
    missing = [item for item in ordered if item not in sites]
    if missing:
        raise ValueError('缺少站源名称定义：%s' % '、'.join(missing))
    return primary, restricted, default, sites


def render_markdown(primary, restricted, default, sites):
    total = len(primary) + len(restricted)
    lines = [
        '### 站源清单（共 %d 个）' % total,
        '',
        '| # | 站源 ID | 名称 | 说明 | 解锁要求 | 所属版本 |',
        '| --- | --- | --- | --- | --- | --- |',
    ]
    index = 0
    for identifier in primary:
        index += 1
        info = sites[identifier]
        lines.append('| %d | `%s` | %s | %s | 无需密码 | 红果鉴 + 真果鉴 |'
                     % (index, identifier, info['name'], info['description']))
    for identifier in restricted:
        index += 1
        info = sites[identifier]
        lines.append('| %d | `%s` | %s | %s | 需解锁密码 | 仅真果鉴 |'
                     % (index, identifier, info['name'], info['description']))
    lines.extend([
        '',
        '- 红果鉴（默认，不带参数）：%d 个站源 —— %s'
        % (len(default), '、'.join(sites[item]['name'] for item in default)),
        '- 真果鉴（`--all-sources`）：%d 个站源 —— 全部' % total,
        '- 默认可见 %d 个；受限 %d 个，需在「站源管理」里用解锁密码显示。'
        % (len(primary), len(restricted)),
        '',
    ])
    return '\n'.join(lines)


def render_json(primary, restricted, default, sites):
    return {
        'total': len(primary) + len(restricted),
        'primaryValues': primary,
        'restrictedValues': restricted,
        'defaultValues': default,
        'sources': [
            {'id': identifier, 'name': sites[identifier]['name'],
             'description': sites[identifier]['description'],
             'visibility': 'primary' if identifier in primary else 'restricted'}
            for identifier in [*primary, *restricted]
        ],
    }


def write_outputs(path, primary, restricted, default):
    entries = [
        ('total', len(primary) + len(restricted)),
        ('primary_count', len(primary)),
        ('restricted_count', len(restricted)),
        ('sources', ' '.join([*primary, *restricted])),
        ('default_sources', ' '.join(default)),
    ]
    with path.open('a', encoding='utf-8') as stream:
        for key, value in entries:
            stream.write('%s=%s\n' % (key, value))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='列出红果鉴 / 真果鉴的全部站源')
    parser.add_argument('--summary', type=Path, help='把 Markdown 追加到 GITHUB_STEP_SUMMARY')
    parser.add_argument('--github-output', type=Path, help='把计数写入 GITHUB_OUTPUT')
    parser.add_argument('--json', action='store_true', help='改用 JSON 输出')
    options = parser.parse_args()
    if not models_path.is_file():
        raise SystemExit('找不到 %s' % models_path)
    primary, restricted, default, sites = collect(models_path.read_text(encoding='utf-8'))
    if options.json:
        print(json.dumps(render_json(primary, restricted, default, sites),
                         ensure_ascii=False, indent=2))
        return
    markdown = render_markdown(primary, restricted, default, sites)
    print(markdown)
    if options.summary:
        with options.summary.open('a', encoding='utf-8') as stream:
            stream.write(markdown + '\n')
    if options.github_output:
        write_outputs(options.github_output, primary, restricted, default)


if __name__ == '__main__':
    main()
