# 压默认 data 目录：
# python -m tool.zip_tool --use-data --task-id TEST_001

# 压指定目录：
# python -m tool.zip_tool --root data/TASK_001 --task-id TASK_001

# 指定输出目录：
# python -m tool.zip_tool --root data/TASK_001 --task-id TASK_001 --out zips

import os
import sys
import argparse
import logging
from typing import List, Optional, Dict

# 允许从仓库根目录直接运行
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import src.global_ctx as ctx
from src.services.uploader import build_zip, build_zip_for_data, zips
from src.utils.logger import sys_logger as logger, log_event


def _parse_meta(meta_items: Optional[List[str]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in meta_items or []:
        if "=" not in item:
            raise ValueError(f"invalid meta item: {item}, expected KEY=VALUE")
        k, v = item.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise ValueError(f"invalid meta key in item: {item}")
        result[k] = v
    return result


def compress_root(
    root_path: str,
    task_id: str,
    meta: Optional[Dict[str, str]] = None,
    zip_output_dir: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
) -> List[str]:
    task = zips(
        root_path=root_path,
        task_id=task_id,
        meta=meta or {},
    )
    return build_zip(
        task,
        zip_output_dir=zip_output_dir,
        include_patterns=include_patterns,
        exclude_dirs=exclude_dirs,
    )


def compress_data(
    task_id: Optional[str] = None,
    meta: Optional[Dict[str, str]] = None,
    zip_output_dir: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_dirs: Optional[List[str]] = None,
) -> List[str]:
    return build_zip_for_data(
        task_id=task_id,
        meta=meta or {},
        zip_output_dir=zip_output_dir,
        include_patterns=include_patterns,
        exclude_dirs=exclude_dirs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PI_CAR zip tool: call uploader.build_zip/build_zip_for_data"
    )
    parser.add_argument(
        "--root",
        type=str,
        default=None,
        help="root path to compress; if omitted and --use-data is set, compress default data root",
    )
    parser.add_argument(
        "--task-id",
        type=str,
        default=None,
        help="task id for zip batch",
    )
    parser.add_argument(
        "--use-data",
        action="store_true",
        help="use build_zip_for_data() to compress default data directory",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="override zip output dir",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=None,
        help="optional include patterns, e.g. --include *.jpg *.json",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=None,
        help="optional exclude dir names, e.g. --exclude logs __pycache__",
    )
    parser.add_argument(
        "--meta",
        nargs="*",
        default=None,
        help="meta items in KEY=VALUE format, e.g. --meta source=manual mode=test",
    )

    args = parser.parse_args()

    try:
        meta = _parse_meta(args.meta)

        log_event(
            logger,
            source="ZIP_TOOL",
            event="compress",
            action="start",
            result="begin",
            key={
                "root": args.root,
                "task_id": args.task_id,
                "use_data": args.use_data,
                "out": args.out,
                "include": args.include,
                "exclude": args.exclude,
                "meta": meta,
            },
            brief=False,
        )

        if args.use_data:
            zip_paths = compress_data(
                task_id=args.task_id,
                meta=meta,
                zip_output_dir=args.out,
                include_patterns=args.include,
                exclude_dirs=args.exclude,
            )
        else:
            if not args.root:
                raise ValueError("either --use-data or --root must be provided")
            if not os.path.exists(args.root):
                raise FileNotFoundError(f"root path not found: {args.root}")
            if not args.task_id:
                raise ValueError("--task-id is required when using --root")

            zip_paths = compress_root(
                root_path=args.root,
                task_id=args.task_id,
                meta=meta,
                zip_output_dir=args.out,
                include_patterns=args.include,
                exclude_dirs=args.exclude,
            )

        log_event(
            logger,
            source="ZIP_TOOL",
            event="compress",
            action="finish",
            result="ok",
            key={
                "count": len(zip_paths),
                "zip_paths": zip_paths,
            },
            ids={"task": args.task_id} if args.task_id else None,
            brief=True,
        )

        print("ZIP_OK")
        for p in zip_paths:
            print(p)
        return 0

    except Exception as e:
        log_event(
            logger,
            source="ZIP_TOOL",
            event="compress",
            action="finish",
            result="fail",
            reason=str(e),
            key={
                "root": args.root,
                "task_id": args.task_id,
                "use_data": args.use_data,
            },
            level=logging.ERROR,
        )
        print(f"ZIP_FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
