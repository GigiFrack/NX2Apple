"""One command for the build the launcher's Build button runs, without the window.

The window is a front end: it composes a `nspb_qt.win.driver` command line, runs it, and then
packs what came out. Both halves are here, in that order, so a shell script or a scheduled job can
do a rebuild on a machine nobody is sitting at:

  build  -> `python -m nspb_qt.win.driver <nsp> -k <keys> --shell-framework <work>/shell-framework`
            (the game's modules as a framework, which is what the shared shell dlopens)
  pack   -> `shell_export.export_shell`, the same call `Model._export_shell_after_archive` makes:
            mirror the framework into the shell's cache, update shell.json, pack the IPA

`--skip-build` packs a framework an earlier run left, which is what a rebuild that only changed
the host's C code needs once the framework is in place. Every option the driver takes is still
reachable through `--` (everything after it is handed to the driver verbatim).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _frontend_qt() -> Path:
    return Path(__file__).resolve().parents[2]


def _repo() -> Path:
    return _frontend_qt().parent


def _load_engine_root(repo: Path) -> Path:
    """`engine.engine_root`: the checkout's engine/, or an engine tree that is the checkout itself."""
    if str(_frontend_qt()) not in sys.path:
        sys.path.insert(0, str(_frontend_qt()))
    from nspb_qt import engine  # noqa: E402  (imported late: the shim is not set up yet)
    return Path(engine.engine_root(str(repo)))


def _load_shell_export():
    if str(_frontend_qt()) not in sys.path:
        sys.path.insert(0, str(_frontend_qt()))
    from nspb_qt.win import shell_export  # noqa: E402
    return shell_export


def _state_dir() -> Path:
    if os.environ.get('NSPB_STATE_DIR'):
        return Path(os.environ['NSPB_STATE_DIR'])
    root = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local'))
    return root / 'NS Port Builder'


def _game_name(se, framework: Path | None, tid: str, engine_root: Path) -> str:
    """The name the shell shows, taken where the launcher takes it."""
    if framework is not None:
        manifest = framework / 'game.manifest.json'
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    name = str(data.get('name') or '').strip()
                    if name:
                        return name
            except (OSError, ValueError):
                pass
    profile = engine_root / 'dm' / 'games' / tid.upper() / 'game.tsv'
    if profile.is_file():
        for line in profile.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.startswith('name\t'):
                name = line[5:].strip()
                if name:
                    return name
    return tid


def _driver_command(args: argparse.Namespace, work: Path | None) -> list[str]:
    cmd = [sys.executable, '-m', 'nspb_qt.win.driver', str(Path(args.nsp_dir).resolve())]
    if args.keys:
        cmd += ['-k', str(Path(args.keys).resolve())]
    if args.title_id:
        cmd += ['--title-id', args.title_id]
    if args.package:
        cmd += ['-p', args.package]
    if work is not None:
        cmd += ['--work-dir', str(work)]
        cmd += ['--shell-framework', str(work / 'shell-framework')]
    cmd += ['--progress', 'lines']
    cmd += list(args.driver_args)
    return cmd


def build_framework(args: argparse.Namespace, work: Path | None) -> int:
    cmd = _driver_command(args, work)
    print('build: ' + ' '.join(cmd), flush=True)
    if work is not None:
        work.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, cwd=str(_frontend_qt()))
    if proc.returncode != 0:
        print(f'build failed: the driver exited {proc.returncode}', file=sys.stderr, flush=True)
    return proc.returncode


def pack(args: argparse.Namespace, work: Path | None, engine_root: Path) -> int:
    if work is None:
        print('pack: no build folder', file=sys.stderr, flush=True)
        return 2
    se = _load_shell_export()
    framework = se.built_framework(str(work))
    if framework is None and args.title_id:
        guess = work / 'shell-framework' / f'DmGame{args.title_id}.framework'
        framework = guess if guess.is_dir() else None
    if framework is None:
        print(f'pack: no built framework in {work / "shell-framework"}', file=sys.stderr, flush=True)
        return 2
    tid = args.title_id or se.title_id_of_framework(framework)
    if not tid:
        print(f'pack: cannot tell the title ID of {framework}', file=sys.stderr, flush=True)
        return 2
    name = args.name or _game_name(se, framework, tid, engine_root)
    root = Path(args.shell_root).expanduser() if args.shell_root else _state_dir() / 'shell'
    print(f'pack: {framework.name} ({tid}, "{name}") into {root}', flush=True)
    ok, ipa, message, notes = se.export_shell(
        root=root,
        repo=engine_root,
        framework=framework,
        title_id=tid,
        name=name,
        python_exe=sys.executable,
        log=lambda line: print(line, end='' if line.endswith('\n') else '\n', flush=True),
    )
    for note in notes:
        print(f'note: {note}', flush=True)
    if not ok:
        print(f'pack failed: {message}', file=sys.stderr, flush=True)
        return 3
    print(f'\nIPA: {ipa}', flush=True)
    if args.print_path_file:
        Path(args.print_path_file).write_text(str(ipa), encoding='utf-8')
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog='python -m nspb_qt.win.build',
        description='Build the shared shell IPA (the game as a framework inside DmShell.app) '
                    'with no launcher window: the driver builds the framework, then it is packed.')
    ap.add_argument('nsp_dir', nargs='?', help="the folder holding the game's NSPs (the launcher's input folder)")
    ap.add_argument('-k', '--keys', help='prod.keys')
    ap.add_argument('--title-id', help='the game to pack when the folder holds several, 16 hex digits')
    ap.add_argument('-p', '--package', help='the version packed, e.g. v458752 (default: the newest in the folder)')
    ap.add_argument('--work-dir', help="the game's build folder (default: <nsp dir>/<title ID>_build)")
    ap.add_argument('--shell-root', help='the shell cache holding DmShell.app, frameworks and shell.json '
                                         '(default: %%LOCALAPPDATA%%\\NS Port Builder\\shell)')
    ap.add_argument('--name', help='the name the shell shows (default: the game profile or the manifest)')
    ap.add_argument('--skip-build', action='store_true', help='pack the framework an earlier run left')
    ap.add_argument('--skip-pack', action='store_true', help='build the framework only')
    ap.add_argument('--print-path-file', help='write the IPA path here, for a script to read back')
    args, passthrough = ap.parse_known_args(argv)
    args.driver_args = passthrough
    if args.driver_args and args.driver_args[0] == '--':
        args.driver_args = args.driver_args[1:]

    if not args.skip_pack and not args.nsp_dir:
        ap.error('nsp_dir is needed unless --skip-pack is given')
    if not args.skip_build and not args.keys:
        print('note: no -k: the folder\'s files must be an unencrypted dump', flush=True)

    repo = _repo()
    engine_root = _load_engine_root(repo)
    work: Path | None = Path(args.work_dir) if args.work_dir else None
    if work is None and args.nsp_dir and args.title_id:
        work = Path(args.nsp_dir).resolve() / f'{args.title_id.upper()}_build'
    if work is None and not args.skip_pack:
        ap.error('packing needs --work-dir (or --title-id, which names the default one)')

    if not args.skip_build:
        rc = build_framework(args, work)
        if rc != 0:
            return rc
    if args.skip_pack:
        return 0
    return pack(args, work, engine_root)


if __name__ == '__main__':
    raise SystemExit(main())
