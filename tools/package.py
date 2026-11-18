#!/usr/bin/env python3
"""将项目核心 Skill 文件打包为标准 .skill 归档包。"""

import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = 'clash-chain-proxy'
DEFAULT_OUTPUT = ROOT / 'dist' / f'{SKILL_NAME}.skill'
# 仅收录 Skill 运行时所需的目录与文件，避免将 README、tests 等开发文件打入包内
INCLUDE_ITEMS = ('SKILL.md', 'agents', 'assets', 'scripts')
EXCLUDED_NAMES = {'__pycache__', '.DS_Store'}
EXCLUDED_SUFFIXES = {'.pyc', '.pyo'}


def collect_skill_files(root: Path = ROOT) -> list[tuple[Path, str]]:
    """收集需要打入 Skill 包的文件及其归档内相对路径。"""
    entries: list[tuple[Path, str]] = []
    for item in INCLUDE_ITEMS:
        target = root / item
        if not target.exists():
            raise FileNotFoundError(f'缺少打包必需的文件或目录：{target}')
        if target.is_file():
            entries.append((target, f'{SKILL_NAME}/{item}'))
            continue
        for file_path in sorted(target.rglob('*')):
            if not file_path.is_file():
                continue
            if any(part in EXCLUDED_NAMES for part in file_path.parts):
                continue
            if file_path.suffix in EXCLUDED_SUFFIXES:
                continue
            rel = file_path.relative_to(root).as_posix()
            entries.append((file_path, f'{SKILL_NAME}/{rel}'))
    return entries


def build_package(output_path: Path = DEFAULT_OUTPUT, root: Path = ROOT) -> list[str]:
    """生成 .skill 压缩包并返回归档内的文件列表。"""
    entries = collect_skill_files(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in entries:
            zf.write(src, arcname=arcname)
            archived.append(arcname)
    return archived


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--output',
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f'输出 .skill 文件路径（默认：{DEFAULT_OUTPUT}）',
    )
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    archived = build_package(output)
    print(f'已生成 {output}（共 {len(archived)} 个文件）：')
    for name in archived:
        print(f'  - {name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
