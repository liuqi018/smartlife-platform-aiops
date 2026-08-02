"""Knowledge base indexing CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.milvus_client import milvus_manager
from app.services.vector_index_service import vector_index_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Index knowledge files into Milvus.")
    parser.add_argument(
        "--index",
        default="data/knowledge",
        help="Directory to recursively index. Default: data/knowledge",
    )
    args = parser.parse_args()

    target = Path(args.index).resolve()
    print(f"扫描目录: {target}")

    milvus_manager.connect()
    result = vector_index_service.index_directory(str(target))

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
