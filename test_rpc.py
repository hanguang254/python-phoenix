
from web3 import Web3
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests

ip = "104.233.194.10"
bsc_test_rpc_url = f"http://{ip}:8545"

# arb_test_rpc_url = f"http://{ip}:8547"

# ava_test_rpc_url = f"http://{ip}:9560/ext/bc/C/rpc"

# bsc_web3 = RpcConnect().connect_rpc(bsc_test_rpc_url)
# print(bsc_web3.is_connected())

# arb_web3 = RpcConnect().connect_rpc(arb_test_rpc_url)
# print(arb_web3.is_connected())

# ava_web3 = RpcConnect().connect_rpc(ava_test_rpc_url)
# print(ava_web3.is_connected())




def percentile(sorted_vals, p: float):
    if not sorted_vals:
        return None
    k = int(round((p / 100.0) * (len(sorted_vals) - 1)))
    k = max(0, min(k, len(sorted_vals) - 1))
    return sorted_vals[k]


def make_session(pool_size: int):
    s = requests.Session()
    # 增加连接池大小以避免阻塞等待
    # pool_connections: 每个主机的连接池数量
    # pool_maxsize: 每个连接池的最大连接数（设置为并发数的2倍，确保不会因连接池满而阻塞）
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=min(pool_size, 100),  # 限制连接池数量，避免过多
        pool_maxsize=pool_size * 3,  # 每个连接池允许更多连接，减少阻塞
        max_retries=0,
        pool_block=True,  # 保持True，但如果连接池足够大，应该不会阻塞
    )
    s.mount("http://", adapter)
    return s


_thread_local = threading.local()


def get_thread_session(pool_size: int):
    if not hasattr(_thread_local, "session"):
        _thread_local.session = make_session(pool_size)
    return _thread_local.session


def worker(thread_id: int, url: str, method: str, params, timeout: float, end_ts: float, pool_size: int,
           results_list: list, lock: threading.Lock,
           err_counters: dict, status_counter: Counter, error_details: dict):
    sess = get_thread_session(pool_size)
    headers = {
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }

    req_id = thread_id * 1_000_000

    while time.monotonic() < end_ts:
        req_id += 1
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        # 记录请求开始时间（在发送请求之前）
        t0 = time.monotonic()
        ok = False
        err_key = None
        http_status = None
        error_msg = None

        try:
            # 发送请求并等待响应（这里会阻塞直到收到响应）
            resp = sess.post(url, json=payload, headers=headers, timeout=timeout)
            http_status = resp.status_code

            if resp.status_code != 200:
                err_key = f"http_{resp.status_code}"
                # 记录 502 错误的详细信息
                if resp.status_code == 502:
                    try:
                        error_msg = resp.text[:200]  # 只取前200个字符
                        # 记录响应头信息，帮助判断是否有代理层
                        server_header = resp.headers.get('Server', '未知')
                        via_header = resp.headers.get('Via', '无')
                        x_powered_by = resp.headers.get('X-Powered-By', '无')
                        error_msg = f"响应体: {error_msg[:150]} | Server: {server_header} | Via: {via_header} | X-Powered-By: {x_powered_by}"
                    except Exception as e:
                        error_msg = f"无法读取响应内容: {str(e)}"
            else:
                # 解析JSON响应（这部分时间也应该计入延迟，因为这是端到端处理的一部分）
                data = resp.json()
                if "error" in data:
                    # JSON-RPC 错误
                    code = data["error"].get("code", "unknown")
                    err_key = f"rpc_error_{code}"
                else:
                    ok = True

        except requests.exceptions.Timeout:
            err_key = "timeout"
        except requests.exceptions.RequestException as e:
            err_key = f"request_exc_{type(e).__name__}"
        except Exception as e:
            err_key = f"exc_{type(e).__name__}"

        # 计算延迟：从请求开始到响应处理完成的总时间（毫秒）
        # 注意：这里计算的是端到端延迟，包括网络传输、服务器处理和JSON解析
        dt = (time.monotonic() - t0) * 1000.0  # ms

        with lock:
            if ok:
                results_list.append(dt)
            else:
                err_counters[err_key] += 1
                # 记录 502 错误的详细信息（只记录前几个）
                if http_status == 502 and error_msg and len(error_details.get("502_details", [])) < 3:
                    if "502_details" not in error_details:
                        error_details["502_details"] = []
                    error_details["502_details"].append(error_msg)
            if http_status is not None:
                status_counter[http_status] += 1


def main():
    ap = argparse.ArgumentParser(description="并发 RPC 压力测试工具（基于 HTTP 的 JSON-RPC）。")
    ap.add_argument("--url", required=True, help="RPC URL，例如：http://104.233.194.10:8545")
    ap.add_argument("--concurrency", type=int, default=100, help="并发线程/用户数量")
    ap.add_argument("--duration", type=int, default=60, help="测试持续时间（秒）")
    ap.add_argument("--timeout", type=float, default=5.0, help="请求超时时间（秒）")
    ap.add_argument("--method", default="eth_blockNumber", help="JSON-RPC 方法")
    ap.add_argument("--params", default="[]", help='JSON 数组字符串，例如："[]" 或 "[\\"latest\\", false]"')
    args = ap.parse_args()

    try:
        params = json.loads(args.params)
        if not isinstance(params, list):
            raise ValueError("params 必须是一个 JSON 数组")
    except Exception as e:
        raise SystemExit(f"无效的 --params 参数: {e}")

    # shared stats
    latencies_ms = []
    lock = threading.Lock()
    err_counters = defaultdict(int)
    status_counter = Counter()
    error_details = {}  # 用于存储错误详细信息

    end_ts = time.monotonic() + args.duration
    pool_size = max(10, args.concurrency)

    print(f"目标地址: {args.url}")
    print(f"方法: {args.method}  参数: {params}")
    print(f"并发数: {args.concurrency}  持续时间: {args.duration}秒  超时: {args.timeout}秒")
    print("正在运行...")

    t_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for i in range(args.concurrency):
            ex.submit(
                worker, i, args.url, args.method, params, args.timeout, end_ts, pool_size,
                latencies_ms, lock, err_counters, status_counter, error_details
            )
    t_end = time.monotonic()
    elapsed = t_end - t_start

    # summarize
    total_ok = len(latencies_ms)
    total_err = sum(err_counters.values())
    total = total_ok + total_err
    qps = total / elapsed if elapsed > 0 else 0.0
    ok_qps = total_ok / elapsed if elapsed > 0 else 0.0

    lat_sorted = sorted(latencies_ms)
    p50 = percentile(lat_sorted, 50)
    p95 = percentile(lat_sorted, 95)
    p99 = percentile(lat_sorted, 99)
    avg = (sum(lat_sorted) / len(lat_sorted)) if lat_sorted else None
    mn = lat_sorted[0] if lat_sorted else None
    mx = lat_sorted[-1] if lat_sorted else None

    print("\n=== 测试结果 ===")
    print(f"耗时: {elapsed:.2f}秒")
    print(f"总请求数: {total}  成功: {total_ok}  错误: {total_err}")
    if total > 0:
        print(f"错误率: {total_err / total * 100:.2f}%")
    print(f"QPS: {qps:.2f}  成功 QPS: {ok_qps:.2f}")

    if avg is not None:
        print("\n成功响应的延迟（毫秒）:")
        print(f"最小值: {mn:.2f}  平均值: {avg:.2f}  P50: {p50:.2f}  P95: {p95:.2f}  P99: {p99:.2f}  最大值: {mx:.2f}")
    else:
        print("\n未记录到成功响应。")

    if status_counter:
        print("\nHTTP 状态码分布:")
        for k, v in status_counter.most_common():
            print(f"  {k}: {v}")

    if err_counters:
        print("\n主要错误:")
        for k, v in sorted(err_counters.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {k}: {v}")
    
    # 显示 502 错误的详细信息
    if err_counters.get("http_502", 0) > 0:
        print("\n" + "="*60)
        print("⚠️  502 Bad Gateway 错误分析")
        print("="*60)
        
        if "502_details" in error_details and error_details["502_details"]:
            print("\n502 错误详细信息（示例）:")
            for i, detail in enumerate(error_details["502_details"], 1):
                print(f"  示例 {i}: {detail}")
        else:
            print("\n⚠️  未能捕获到 502 错误的详细响应内容")
        
        print(f"\n📊 统计: 共出现 {err_counters.get('http_502', 0)} 次 502 错误")
        print(f"   错误率: {err_counters.get('http_502', 0) / total * 100:.2f}%")
        
        print("\n💡 即使服务器没有显式网关，502 错误仍可能出现的原因:")
        print("\n  1. 【RPC 框架/实现问题】")
        print("     - 某些 RPC 节点实现（如 Geth、Erigon）在特定情况下可能返回 502")
        print("     - 当节点同步、重启或内部错误时，HTTP 层可能返回 502")
        print("     - 某些 RPC 框架的错误处理机制可能将内部错误映射为 502")
        print("\n  2. 【HTTP 服务器库行为】")
        print("     - 如果 RPC 服务使用 HTTP 服务器库（如 Go 的 net/http）")
        print("     - 当后端处理程序崩溃或超时时，服务器可能返回 502")
        print("     - 这是 HTTP 服务器库的标准行为，不是网关问题")
        print("\n  3. 【隐藏的代理层】")
        print("     - 服务器可能配置了内部反向代理（即使管理员不知道）")
        print("     - 容器化部署（Docker/K8s）通常有 ingress/负载均衡器")
        print("     - 云服务提供商可能自动添加了代理层")
        print("     - 检查响应头中的 'Server'、'Via'、'X-Powered-By' 字段可帮助判断")
        print("\n  4. 【服务器过载/资源耗尽】")
        print("     - 高并发压力（当前并发: {}）可能导致服务器资源耗尽".format(args.concurrency))
        print("     - CPU/内存/文件描述符达到上限时，服务器可能返回 502")
        print("     - 特别是 eth_getLogs 这类查询可能较耗时，容易触发")
        print("\n  5. 【网络/连接问题】")
        print("     - TCP 连接异常中断时，某些服务器实现可能返回 502")
        print("     - 服务器与数据库/存储层连接失败时也可能返回 502")
        print("\n🔍 诊断建议:")
        print("  - 查看上面的响应头信息（Server、Via、X-Powered-By）")
        print("  - 如果 'Via' 或 'X-Forwarded-For' 存在，说明有代理层")
        print("  - 如果 'Server' 显示 Nginx/Apache/Caddy，说明有反向代理")
        print("  - 检查服务器日志，查看 502 错误的具体原因")
        print("  - 降低并发数测试，看是否还会出现 502")
        print("="*60)


if __name__ == "__main__":
    main()



