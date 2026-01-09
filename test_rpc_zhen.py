#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RPC节点调用脚本 - 调用 eth_getLogs 并输出延迟数据
"""

import json
import time
import requests
import argparse
from typing import List, Optional


def call_eth_getLogs(url: str, address: str, params: Optional[dict] = None) -> dict:
    """
    调用 eth_getLogs 方法
    
    Args:
        url: RPC节点地址
        address: 合约地址
        params: 额外的查询参数（如 fromBlock, toBlock, topics等）
    
    Returns:
        包含响应数据和延迟信息的字典
    """
    # 构建参数
    filter_params = {"address": address}
    if params:
        filter_params.update(params)
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [filter_params]
    }
    
    headers = {
        "Content-Type": "application/json",
        "Connection": "keep-alive"
    }
    
    result = {
        "success": False,
        "latency_ms": None,
        "data": None,
        "error": None,
        "http_status": None
    }
    
    # 记录开始时间
    start_time = time.monotonic()
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        
        # 记录结束时间（收到HTTP响应的时间）
        end_time = time.monotonic()
        latency_ms = (end_time - start_time) * 1000.0
        
        result["latency_ms"] = latency_ms
        result["http_status"] = response.status_code
        
        if response.status_code != 200:
            result["error"] = f"HTTP错误: {response.status_code}"
            try:
                result["error"] += f" - {response.text[:200]}"
            except:
                pass
            return result
        
        # 解析JSON响应
        try:
            data = response.json()
            
            if "error" in data:
                result["error"] = f"RPC错误: {data['error']}"
                return result
            
            result["success"] = True
            result["data"] = data.get("result", [])
            return result
            
        except json.JSONDecodeError as e:
            result["error"] = f"JSON解析错误: {e}"
            return result
            
    except requests.exceptions.Timeout:
        end_time = time.monotonic()
        latency_ms = (end_time - start_time) * 1000.0
        result["latency_ms"] = latency_ms
        result["error"] = "请求超时"
        return result
        
    except requests.exceptions.ConnectionError as e:
        end_time = time.monotonic()
        latency_ms = (end_time - start_time) * 1000.0
        result["latency_ms"] = latency_ms
        result["error"] = f"连接错误: {e}"
        return result
        
    except Exception as e:
        end_time = time.monotonic()
        latency_ms = (end_time - start_time) * 1000.0
        result["latency_ms"] = latency_ms
        result["error"] = f"异常: {type(e).__name__}: {e}"
        return result


def calculate_stats(latencies: List[float]) -> dict:
    """计算延迟统计信息"""
    if not latencies:
        return {}
    
    sorted_latencies = sorted(latencies)
    n = len(sorted_latencies)
    
    def percentile(p: float) -> float:
        k = int(round((p / 100.0) * (n - 1)))
        k = max(0, min(k, n - 1))
        return sorted_latencies[k]
    
    return {
        "count": n,
        "min": sorted_latencies[0],
        "max": sorted_latencies[-1],
        "avg": sum(sorted_latencies) / n,
        "p50": percentile(50),
        "p90": percentile(90),
        "p95": percentile(95),
        "p99": percentile(99)
    }


def main():
    parser = argparse.ArgumentParser(
        description="调用 eth_getLogs 并输出延迟数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 单次调用
  python test_rpc_zhen.py --url http://104.233.194.10:8545 --address 0x9244212403a2e827cadca1f6fb68b43bc0c7a41f
  
  # 多次调用并统计延迟
  python test_rpc_zhen.py --url http://104.233.194.10:8545 --address 0x9244212403a2e827cadca1f6fb68b43bc0c7a41f --count 10
        """
    )
    
    parser.add_argument(
        "--url",
        default="http://104.233.194.10:8545",
        help="RPC节点URL（默认: http://104.233.194.10:8545）"
    )
    parser.add_argument(
        "--address",
        default="0x9244212403a2e827cadca1f6fb68b43bc0c7a41f",
        help="合约地址（默认: 0x9244212403a2e827cadca1f6fb68b43bc0c7a41f）"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="调用次数（默认: 1，用于统计延迟）"
    )
    parser.add_argument(
        "--params",
        default=None,
        help="额外的查询参数（JSON格式），例如: '{\"fromBlock\":\"0x0\",\"toBlock\":\"latest\"}'"
    )
    
    args = parser.parse_args()
    
    # 解析额外参数
    extra_params = {}
    if args.params:
        try:
            extra_params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(f"错误: 无效的JSON参数格式: {e}")
            return 1
    
    print("=" * 70)
    print("RPC节点调用 - eth_getLogs")
    print("=" * 70)
    print(f"节点URL: {args.url}")
    print(f"合约地址: {args.address}")
    print(f"调用次数: {args.count}")
    if extra_params:
        print(f"额外参数: {extra_params}")
    print("=" * 70)
    
    latencies = []
    success_count = 0
    error_count = 0
    
    for i in range(args.count):
        print(f"\n第 {i+1}/{args.count} 次调用...")
        
        result = call_eth_getLogs(args.url, args.address, extra_params)
        
        if result["success"]:
            success_count += 1
            latency = result["latency_ms"]
            latencies.append(latency)
            log_count = len(result["data"]) if result["data"] else 0
            
            print(f"  ✓ 成功")
            print(f"  延迟: {latency:.2f}ms")
            print(f"  返回日志数量: {log_count}")
            
            # 如果是单次调用，显示部分日志详情
            if args.count == 1 and result["data"]:
                print(f"\n  日志详情（前3条）:")
                for idx, log in enumerate(result["data"][:3], 1):
                    print(f"    日志 {idx}:")
                    print(f"      blockNumber: {log.get('blockNumber', 'N/A')}")
                    print(f"      transactionHash: {log.get('transactionHash', 'N/A')}")
                    print(f"      topics: {log.get('topics', [])}")
        else:
            error_count += 1
            latency = result["latency_ms"] if result["latency_ms"] else 0
            print(f"  ✗ 失败")
            print(f"  延迟: {latency:.2f}ms" if latency else "  延迟: N/A")
            print(f"  错误: {result['error']}")
        
        # 如果不是最后一次，稍作延迟
        if i < args.count - 1:
            time.sleep(0.1)
    
    # 输出统计信息
    print("\n" + "=" * 70)
    print("统计结果")
    print("=" * 70)
    print(f"总调用次数: {args.count}")
    print(f"成功: {success_count} ({success_count/args.count*100:.2f}%)")
    print(f"失败: {error_count} ({error_count/args.count*100:.2f}%)")
    
    if latencies:
        stats = calculate_stats(latencies)
        print(f"\n延迟数据统计（单位：毫秒）:")
        print(f"  调用次数: {stats['count']}")
        print(f"  最小值: {stats['min']:.2f}ms")
        print(f"  最大值: {stats['max']:.2f}ms")
        print(f"  平均值: {stats['avg']:.2f}ms")
        print(f"  P50 (中位数): {stats['p50']:.2f}ms")
        print(f"  P90: {stats['p90']:.2f}ms")
        print(f"  P95: {stats['p95']:.2f}ms")
        print(f"  P99: {stats['p99']:.2f}ms")
    else:
        print("\n无成功调用，无法统计延迟数据")
    
    print("=" * 70)
    
    return 0


if __name__ == "__main__":
    exit(main())
