"""从 ithtelab/sr-adblock 的 release 产物生成「B站 gRPC 不解密」补丁模块。

上游把 grpc.biliapi.net 放进 [MITM]，Shadowrocket 解密 gRPC(HTTP/2) 很慢，
导致 B 站视频简介页、评论区加载慢甚至失败。本脚本做三件事：

  1. 删掉 bilibili.request 脚本（在 JS 内重发 View/View 与 Reply/MainList，
     timeout=3s、双域名重试、失败即中止）
  2. 把 grpc.biliapi.net 从 [MITM] 里摘掉
  3. 改模块名，便于和官方版区分

规则条数必须与上游完全一致，否则退出码 2，不产出坏文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

UPSTREAM = "https://raw.githubusercontent.com/ithtelab/sr-adblock/release/module/ads-all.srmodule"
MIRRORS = [
    "",
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
]
OUT = Path(__file__).resolve().parent.parent / "out" / "ads-all-bili-nomitm.srmodule"
LABEL = "B站grpc不解密版"
NOTE = "\n".join([
    "# ==========================================================================",
    "# [fix] 由 GitHub Actions 自动生成（patch/autopatch.py）：",
    "#   1) 删除 bilibili.request —— http-request 在 JS 内重发 View/View 与 Reply/MainList，",
    "#      timeout=3 + 双域名重试 + 失败即中止，只会放大延迟，不是净化所必需。",
    "#   2) 把 grpc.biliapi.net 从 [MITM] 摘除 —— 小火箭对 gRPC(HTTP/2) 解密本身很慢，",
    "#      这是「视频简介 / 评论区」加载慢的真正原因。",
    "#   3) 开屏、会员购、首页、账号页走 app.bilibili.com，仍然解密+改写，不受影响。",
    "# ==========================================================================",
])


def fetch(source: str) -> str:
    if source != UPSTREAM:
        if "://" not in source:
            text = Path(source).read_text(encoding="utf-8")
            if "[Rule]" not in text:
                raise SystemExit(f"本地文件不像模块：{source}")
            print(f"读取本地文件 {len(text):,} 字符 <- {source}")
            return text
        mirrors = [""]
        target = source
    else:
        mirrors = MIRRORS
        target = UPSTREAM

    last = None
    for prefix in mirrors:
        url = prefix + target
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sr-adblock-bili-patch/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                text = r.read().decode("utf-8")
            if "[Rule]" not in text or "bilibili.protobuf" not in text:
                raise ValueError("内容校验失败：不是预期的 sr-adblock 模块")
            if len(text) < 100_000:
                raise ValueError(f"文件太小（{len(text)} 字符）")
            print(f"下载成功 {len(text):,} 字符 <- {url}")
            return text
        except Exception as exc:
            last = f"{url} -> {exc}"
            print(f"下载失败: {last}", file=sys.stderr)
    raise SystemExit(f"所有下载源都失败，最后一次：{last}")


def stats(text: str):
    scripts = text.count("type=http-")
    rules, in_rule = 0, False
    for line in text.split("\n"):
        s = line.strip()
        if re.match(r"^\[[^\]]+\]$", s):
            in_rule = s == "[Rule]"
            continue
        if in_rule and s and not s.startswith("#"):
            rules += 1
    return scripts, rules


def patch(text: str):
    lines = []
    dropped = 0
    for line in text.split("\n"):
        if line.startswith("bilibili.request = "):
            dropped += 1
            continue
        lines.append(line)

    mitm = 0
    for i, line in enumerate(lines):
        if line.startswith("hostname") and "grpc.biliapi.net" in line:
            new = line.replace("grpc.biliapi.net, ", "").replace(", grpc.biliapi.net", "")
            if new != line:
                lines[i] = new
                mitm += 1

    for i, line in enumerate(lines):
        if line.startswith("#!name="):
            name = line[len("#!name="):]
            if LABEL not in name:
                if name.endswith("）"):
                    name = name[:-1] + "·" + LABEL + "）"
                else:
                    name = name + "（" + LABEL + "）"
                lines[i] = "#!name=" + name
            break

    for i, line in enumerate(lines):
        if line.startswith("["):
            lines.insert(i, NOTE)
            break

    return "\n".join(lines), dropped, mitm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=UPSTREAM, help="上游 URL，或本地文件路径（调试用）")
    args = ap.parse_args()
    text = fetch(args.source)
    before = stats(text)
    m = re.search(r"(?m)^#!update=(.+)$", text)
    upstream_version = m.group(1).strip() if m else "unknown"
    print(f"上游: 脚本 {before[0]} 条, 规则 {before[1]} 条, 版本 {upstream_version}")

    result, dropped, mitm = patch(text)
    after = stats(result)
    print(f"补丁: 删脚本 {dropped} 条, 摘 MITM 主机 {mitm} 处 -> 脚本 {after[0]} 条, 规则 {after[1]} 条")

    if before[1] != after[1]:
        print(f"规则条数不一致（{before[1]} -> {after[1]}），拒绝产出", file=sys.stderr)
        return 2
    if before[0] - after[0] != 1:
        print("脚本条数变化不是 1，上游结构可能改了，需人工检查", file=sys.stderr)
        return 2
    if mitm == 0:
        print("警告：MITM 里没找到 grpc.biliapi.net，上游可能已改结构", file=sys.stderr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists() and OUT.read_text(encoding="utf-8") == result:
        print("内容与现有文件一致，跳过写入")
    else:
        OUT.write_text(result, encoding="utf-8", newline="")
        print(f"已写入 {OUT}（{OUT.stat().st_size:,} 字节）")

    meta = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "upstream": UPSTREAM,
        "upstream_version": upstream_version,
        "rule_count": after[1],
        "script_count": after[0],
        "mitm_host_removed": "grpc.biliapi.net" if mitm else None,
    }
    (OUT.parent / "status.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
