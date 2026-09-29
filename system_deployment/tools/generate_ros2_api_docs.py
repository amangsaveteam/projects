#!/usr/bin/env python3
"""Collect ROS 2 runtime graph interfaces into JSON and Markdown.

Run this on a device with the target ROS environment sourced. It intentionally
uses the public ros2 CLI so it works across Humble/Jazzy without Python package
introspection dependencies.
"""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path


def run(*args: str) -> str:
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def typed_lines(output: str) -> list[dict[str, str]]:
    rows=[]
    for line in output.splitlines():
        line=line.strip()
        if not line or line.startswith("/") is False: continue
        if " [" in line and line.endswith("]"):
            name, typ=line.rsplit(" [",1)
            rows.append({"name":name, "type":typ[:-1]})
        else:
            rows.append({"name":line})
    return rows


def plain_lines(output: str) -> list[str]:
    return [x.strip() for x in output.splitlines() if x.strip() and not x.startswith("WARNING")]


def collect() -> dict:
    data={"ros2_cli":run("ros2","--version").strip(), "nodes":plain_lines(run("ros2","node","list"))}
    data["topics"]=typed_lines(run("ros2","topic","list","-t"))
    data["services"]=typed_lines(run("ros2","service","list","-t"))
    data["actions"]=typed_lines(run("ros2","action","list","-t"))
    return data


def markdown(data: dict) -> str:
    out=["# ROS 2 模块接口", "", "> 此文档由 `generate_ros2_api_docs.py` 从运行中的 ROS 2 graph 自动生成。", "", f"ROS CLI：`{data['ros2_cli']}`", "", "## 节点", ""]
    out += [f"- `{n}`" for n in data["nodes"]] or ["- 未发现节点"]
    for title,key in (("Topic","topics"),("Service","services"),("Action","actions")):
        out += ["", f"## {title}", "", "| 名称 | 类型 |", "|---|---|"]
        out += [f"| `{x['name']}` | `{x.get('type','未返回')}` |" for x in data[key]] or ["| 未发现 | - |"]
    return "\n".join(out)+"\n"


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, default=Path("ros2-api"))
    args=ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data=collect()
    (args.output_dir/"ros2-api.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
    (args.output_dir/"ROS2_API.md").write_text(markdown(data),encoding="utf-8")
    print(args.output_dir/"ROS2_API.md")

if __name__ == "__main__": main()
