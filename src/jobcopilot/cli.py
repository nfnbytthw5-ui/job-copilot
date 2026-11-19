"""命令行入口：run / report。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .runner import Runner


def _load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"配置文件不存在：{path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def cmd_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    repo_root = config_path.parent
    config = _load_config(config_path)

    runner = Runner(config, repo_root, dry_run=args.dry_run)
    summary = runner.run()

    print(f"[{summary['date']}] 目标 {summary['target']} / 实际 {summary['actual']}"
          f" / 偏差 {summary['deviation']}")
    for p in summary["platforms"]:
        print(f"  - {p['platform']}: {p['status']} 取岗{p.get('found', 0)} {p.get('counts', {})}"
              + (f"（{p['reason']}）" if p.get("reason") else ""))
    print(f"战报已写入 {repo_root / 'reports'}")
    return 0 if all(p["status"] == "ok" for p in summary["platforms"]) else 2


def cmd_report(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    repo_root = config_path.parent
    config = _load_config(config_path)
    reports_dir = repo_root / config.get("paths", {}).get("reports_dir", "reports")

    if args.date:
        json_path = reports_dir / f"{args.date}.json"
        if not json_path.exists():
            raise SystemExit(f"没有找到 {json_path}")
        print(json_path.read_text(encoding="utf-8"))
        return 0

    files = sorted(reports_dir.glob("*.json"))
    if not files:
        print("还没有战报，先跑一次 run")
        return 0
    for path in files[-7:]:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"{data['date']}  实际 {data['actual']}/{data['target']}"
              f"  拦截 {data['run_counts'].get('blocked', 0)}"
              f"  失败 {data['run_counts'].get('failed', 0)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobcopilot", description="求职投递编排器")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="执行一轮投递")
    run.add_argument("--config", default="config.yaml", help="配置文件路径")
    run.add_argument("--dry-run", action="store_true", help="不产生真实投递")
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("report", help="查看战报")
    report.add_argument("--config", default="config.yaml", help="配置文件路径")
    report.add_argument("--date", help="指定日期 YYYY-MM-DD，缺省列出最近 7 天")
    report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
