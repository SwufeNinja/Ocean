from __future__ import annotations

import argparse
from pathlib import Path

from ocean.config import load_config
from ocean.pipeline import run_keyword_extraction, run_ocr, run_semantic_extraction


def main() -> None:
    parser = argparse.ArgumentParser(prog="ocean", description="Batch PDF OCR and text extraction toolkit.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ocr_parser = subparsers.add_parser("ocr", help="Run OCR for a PDF file or directory.")
    ocr_parser.add_argument("--input", required=True, help="PDF file or directory containing PDF files.")
    ocr_parser.add_argument("--output", required=True, help="Output directory.")
    ocr_parser.add_argument("--config", required=True, help="YAML config path.")

    web_parser = subparsers.add_parser("web", help="Start the MinerU upload and Markdown viewer web UI.")
    web_parser.add_argument("--config", required=True, help="YAML config path.")
    web_parser.add_argument("--output", default="./outputs", help="Output directory.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Web server host.")
    web_parser.add_argument("--port", type=int, default=8000, help="Web server port.")

    keyword_parser = subparsers.add_parser("extract-keywords", help="Extract keyword-matched paragraphs.")
    keyword_parser.add_argument("--ocr-dir", required=True, help="Directory containing OCR JSON files.")
    keyword_parser.add_argument("--output", required=True, help="Output directory.")
    keyword_parser.add_argument("--config", required=True, help="YAML config path.")

    semantic_parser = subparsers.add_parser("extract-semantic", help="Extract topic-related text with LLM.")
    semantic_parser.add_argument("--ocr-dir", required=True, help="Directory containing OCR JSON files.")
    semantic_parser.add_argument("--output", required=True, help="Output directory.")
    semantic_parser.add_argument("--config", required=True, help="YAML config path.")

    args = parser.parse_args()
    config = load_config(Path(args.config))

    if args.command == "ocr":
        documents = run_ocr(args.input, args.output, config)
        print(f"OCR completed: {len(documents)} document(s).")
    elif args.command == "web":
        from ocean.web import serve

        serve(config=config, output_dir=args.output, host=args.host, port=args.port)
    elif args.command == "extract-keywords":
        results = run_keyword_extraction(args.ocr_dir, args.output, config)
        print(f"Keyword extraction completed: {len(results)} result(s).")
    elif args.command == "extract-semantic":
        results = run_semantic_extraction(args.ocr_dir, args.output, config)
        print(f"Semantic extraction completed: {len(results)} result(s).")
    else:  # pragma: no cover
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
