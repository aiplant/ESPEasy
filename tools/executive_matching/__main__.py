"""
CLI entry point for the executive-business school matching tool.
命令行入口

Usage:
    python -m tools.executive_matching --help
    python -m tools.executive_matching run --bids bids.json --output results.xlsx
    python -m tools.executive_matching run --companies "公司A,公司B" --format csv
    python -m tools.executive_matching init-config --output config.json
"""

import argparse
import json
import logging
import sys

from .config import PipelineConfig
from .export import generate_summary_stats
from .pipeline import Pipeline


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_run(args: argparse.Namespace) -> None:
    """Execute the matching pipeline."""
    # Load config
    if args.config:
        config = PipelineConfig.from_file(args.config)
    else:
        config = PipelineConfig.from_env()

    if args.output_dir:
        config.output_dir = args.output_dir

    pipeline = Pipeline(config)

    # Load companies
    if args.bids:
        count = pipeline.load_companies_from_bid_list(args.bids)
        logging.info("Loaded %d companies from bid list", count)
    elif args.companies:
        names = [n.strip() for n in args.companies.split(",") if n.strip()]
        count = pipeline.load_companies_from_names(names)
        logging.info("Loaded %d companies from argument", count)
    else:
        logging.error("Must provide --bids or --companies")
        sys.exit(1)

    # Load alumni data if provided
    if args.alumni:
        pipeline.load_alumni_data(args.alumni)

    # Run pipeline
    results = pipeline.run()

    # Export
    fmt = args.format or "excel"
    output_path = pipeline.export(results, fmt=fmt, filename=args.filename)
    print(f"\nExported to: {output_path}")

    # Print summary
    stats = generate_summary_stats(results)
    print("\n===== 匹配结果摘要 =====")
    print(f"企业数量: {stats['total_companies']}")
    print(f"高管总数: {stats['total_executives']}")
    print(f"已匹配:   {stats['matched_executives']}")
    print(f"未匹配:   {stats['unmatched_executives']}")
    print(f"匹配率:   {stats['match_rate']}")

    if stats["school_distribution"]:
        print("\n商学院分布:")
        for school, count in stats["school_distribution"].items():
            print(f"  {school}: {count}")

    if stats["program_distribution"]:
        print("\n项目类型分布:")
        for program, count in stats["program_distribution"].items():
            print(f"  {program}: {count}")


def cmd_init_config(args: argparse.Namespace) -> None:
    """Generate a sample configuration file."""
    config = PipelineConfig()
    output = args.output or "config.json"
    config.to_file(output)
    print(f"Sample config written to: {output}")
    print("Edit the file to add your API keys and adjust settings.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="高管画像与商学院匹配系统 - Executive-Business School Matching Tool",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    subparsers = parser.add_subparsers(dest="command")

    # run command
    run_parser = subparsers.add_parser("run", help="执行匹配管道")
    run_parser.add_argument(
        "--config", "-c", help="配置文件路径 (JSON)"
    )
    run_parser.add_argument(
        "--bids", "-b", help="招标/中标企业列表 (JSON)"
    )
    run_parser.add_argument(
        "--companies", help="逗号分隔的企业名称列表"
    )
    run_parser.add_argument(
        "--alumni", "-a", help="校友录数据文件 (JSON)"
    )
    run_parser.add_argument(
        "--format", "-f", choices=["json", "csv", "excel"], default="excel",
        help="输出格式 (默认: excel)",
    )
    run_parser.add_argument(
        "--output-dir", "-o", help="输出目录"
    )
    run_parser.add_argument(
        "--filename", help="输出文件名"
    )

    # init-config command
    init_parser = subparsers.add_parser("init-config", help="生成示例配置文件")
    init_parser.add_argument(
        "--output", "-o", default="config.json", help="输出路径"
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "run":
        cmd_run(args)
    elif args.command == "init-config":
        cmd_init_config(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
