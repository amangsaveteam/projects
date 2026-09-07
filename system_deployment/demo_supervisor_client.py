#!/usr/bin/env python3
"""Supervisor Agent API 调用示例 - 演示其他项目后端如何使用现有 HTTP 接口。"""

import json
import sys
import urllib.request
import urllib.error

# Supervisor Agent 地址（Orin 设备默认 9080 端口）
AGENT_URL = "http://172.16.9.91:9080"


def api_get(path):
    """发送 GET 请求并返回 JSON 响应。"""
    url = AGENT_URL + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(path):
    """发送 POST 请求并返回 JSON 响应。"""
    url = AGENT_URL + path
    req = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def demo_health():
    """1. 健康检查"""
    print("=" * 60)
    print("1. 健康检查: GET /api/v1/health")
    print("=" * 60)
    result = api_get("/api/v1/health")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()


def demo_list_modules():
    """2. 获取所有模块和进程状态"""
    print("=" * 60)
    print("2. 模块列表: GET /api/v1/modules")
    print("=" * 60)
    result = api_get("/api/v1/modules")
    device = result.get("device", "unknown")
    modules = result.get("modules", [])
    print("设备: {}".format(device))
    print("模块数量: {}".format(len(modules)))
    print()
    for module in modules:
        status = "可达" if module.get("reachable") else "不可达"
        print("  [{}] {}/{} (id={})".format(
            status,
            module.get("device", "?"),
            module.get("name", "?"),
            module.get("id", "?"),
        ))
        if not module.get("reachable"):
            print("         错误: {}".format(module.get("error", "未知")))
        for proc in module.get("processes", []):
            print("         进程: {}  状态: {}  PID: {}".format(
                proc["name"], proc["state"], proc["pid"],
            ))
    print()
    return modules


def demo_process_action(module_id, process_name, action):
    """3. 控制进程（启动/停止/重启）"""
    print("=" * 60)
    print("3. 进程控制: POST /api/v1/modules/{}/processes/{}/{}".format(
        module_id, process_name, action,
    ))
    print("=" * 60)
    result = api_post("/api/v1/modules/{}/processes/{}/{}".format(
        module_id, process_name, action,
    ))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()


def demo_process_log(module_id, process_name):
    """4. 获取进程日志尾部"""
    print("=" * 60)
    print("4. 进程日志: GET /api/v1/modules/{}/processes/{}/log".format(
        module_id, process_name,
    ))
    print("=" * 60)
    result = api_get("/api/v1/modules/{}/processes/{}/log?offset=0&length=2048".format(
        module_id, process_name,
    ))
    log_data = result.get("data", "")
    print("日志长度: {} 字符".format(len(log_data)))
    print("下一个偏移: {}".format(result.get("next_offset", 0)))
    print("--- 日志内容（最后 500 字符）---")
    print(log_data[-500:] if len(log_data) > 500 else log_data)
    print()


def main():
    global AGENT_URL
    if len(sys.argv) > 1:
        AGENT_URL = sys.argv[1].rstrip("/")
    print("目标 Agent: {}".format(AGENT_URL))
    print()

    # 1. 健康检查
    try:
        demo_health()
    except (urllib.error.URLError, OSError) as e:
        print("连接失败: {}".format(e))
        print("请确认 Supervisor Agent 正在运行并监听该地址。")
        return 1

    # 2. 列出所有模块和进程
    try:
        modules = demo_list_modules()
    except (urllib.error.URLError, OSError) as e:
        print("获取模块列表失败: {}".format(e))
        return 1

    # 3. 用第一个可达模块的第一个进程演示日志和控制
    for module in modules:
        if not module.get("reachable") or not module.get("processes"):
            continue
        mid = module["id"]
        proc = module["processes"][0]["name"]

        # 演示获取日志
        try:
            demo_process_log(mid, proc)
        except (urllib.error.URLError, OSError) as e:
            print("获取日志失败: {}".format(e))

        # 演示重启（取消注释以下行实际执行）
        # demo_process_action(mid, proc, "restart")
        print("提示: 取消注释 demo_process_action 行可实际执行进程控制操作")
        break

    print("=" * 60)
    print("演示完成! 以上接口均可供其他项目后端直接 HTTP 调用。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
