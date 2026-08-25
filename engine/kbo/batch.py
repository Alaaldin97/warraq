"""Batch runner: convert every PDF in a folder for the Kindle Oasis.

Usage:
  python -m kbo.batch [--inbox DIR] [--out DIR] [--workers N] [--max-pages N]
"""
from __future__ import annotations

import argparse
import os
import shutil
import traceback

from . import cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "INBOX")
OUT = os.path.join(ROOT, "output")
DELIVERY = os.path.join(ROOT, "DELIVERY")
DONE = os.path.join(ROOT, "INBOX", "_converted")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--inbox", default=INBOX)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--delivery", default=DELIVERY)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--force-route", default="auto",
                    choices=["auto", "reflow", "ocr", "fixed"])
    a = ap.parse_args(argv)

    os.makedirs(a.delivery, exist_ok=True)
    os.makedirs(DONE, exist_ok=True)
    pdfs = sorted(f for f in os.listdir(a.inbox)
                  if f.lower().endswith(".pdf")
                  and os.path.isfile(os.path.join(a.inbox, f)))
    if not pdfs:
        print(f"No PDFs found in {a.inbox}")
        return 1

    print(f"Found {len(pdfs)} PDF(s) in {a.inbox}\n")
    summary = []
    for i, name in enumerate(pdfs, 1):
        src = os.path.join(a.inbox, name)
        slug = cli.safe_name(os.path.splitext(name)[0])
        outdir = os.path.join(a.out, slug)
        print("=" * 72)
        print(f"[{i}/{len(pdfs)}] {name}")
        print("=" * 72)
        try:
            res = cli.run(src, outdir, a.max_pages, a.force_route,
                          False, "auto", a.workers)
            for k, p in res["outputs"].items():
                if os.path.exists(p):
                    shutil.copy2(p, os.path.join(a.delivery, os.path.basename(p)))
            summary.append({
                "file": name,
                "title": res["meta"]["title"],
                "pages": res["analysis"]["page_count"],
                "language": res["analysis"].get("language"),
                "doc_type": res["analysis"]["doc_type"],
                "route": res["route"],
                "ocr_conf": res.get("ocr", {}).get("mean_conf"),
                "qa": res.get("qa_gate"),
                "review_pages": len(res.get("manual_review_pages", [])),
                "outputs": [os.path.basename(p) for p in res["outputs"].values()],
                "seconds": res["elapsed_sec"],
            })
            try:
                shutil.move(src, os.path.join(DONE, name))
            except Exception:
                pass
        except Exception:
            traceback.print_exc()
            summary.append({"file": name, "qa": "ERROR"})

    print("\n" + "=" * 72)
    print("BATCH SUMMARY")
    print("=" * 72)
    for s in summary:
        if s.get("qa") == "ERROR":
            print(f"  {s['file']}: FAILED")
            continue
        print(f"  {s['title']}  [{s['language']}/{s['doc_type']}] "
              f"{s['pages']}p  route={s['route']}  "
              f"ocr={s['ocr_conf']}  QA={s['qa']}  "
              f"review={s['review_pages']}  {s['seconds']}s")
    print(f"\nAll files copied to: {a.delivery}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
