#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RPC节点压力测试脚本
支持统计延迟数据、成功数据、超时数据等
"""

import argparse
import json
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class RPCStressTest:
    """RPC节点压力测试类"""
    
    def __init__(self, url: str, method: str, params: list, timeout: float, 
                 concurrency: int, duration: int):
        self.url = url
        self.method = method
        self.params = params
        self.timeout = timeout
        self.concurrency = concurrency
        self.duration = duration
        
        # 统计数据结构
        self.lock = threading.Lock()
        self.success_latencies: List[float] = []  # 成功请求的延迟（毫秒）
        self.timeout_count = 0  # 超时请求数
        self.success_count = 0  # 成功请求数
        self.error_count = 0  # 总错误数
        self.error_details: Dict[str, int] = defaultdict(int)  # 错误详情统计
        self.http_status_codes: Counter = Counter()  # HTTP状态码统计
        self.rpc_errors: Counter = Counter()  # RPC错误统计
        
        # 线程本地存储session
        self._thread_local = threading.local()
        
    def _get_session(self) -> requests.Session:
        """获取线程本地的session"""
        if not hasattr(self._thread_local, 'session'):
            session = requests.Session()
            # 配置连接池
            adapter = HTTPAdapter(
                pool_connections=self.concurrency,
                pool_maxsize=self.concurrency * 2,
                max_retries=0,  # 不自动重试
            )
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            self._thread_local.session = session
        return self._thread_local.session
    
    def _make_request(self, request_id: int) -> Dict:
        """发送单个RPC请求"""
        session = self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": self.method,
            "params": self.params
        }
        headers = {
            "Content-Type": "application/json",
            "Connection": "keep-alive"
        }
        
        result = {
            "success": False,
            "latency_ms": None,
            "error_type": None,
            "http_status": None,
            "rpc_error_code": None,
            "timeout": False
        }
        
        start_time = time.monotonic()
        
        try:
            response = session.post(
                self.url,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            end_time = time.monotonic()
            latency_ms = (end_time - start_time) * 1000.0
            result["latency_ms"] = latency_ms
            result["http_status"] = response.status_code
            
            if response.status_code != 200:
                result["error_type"] = f"http_{response.status_code}"
                return result
            
            # 解析JSON响应
            try:
                data = response.json()
                if "error" in data:
                    # RPC错误
                    error_code = data["error"].get("code", "unknown")
                    result["error_type"] = f"rpc_error_{error_code}"
                    result["rpc_error_code"] = error_code
                    return result
                else:
                    # 成功
                    result["success"] = True
                    return result
                    
            except json.JSONDecodeError:
                result["error_type"] = "json_decode_error"
                return result
                
        except requests.exceptions.Timeout:
            end_time = time.monotonic()
            latency_ms = (end_time - start_time) * 1000.0
            result["latency_ms"] = latency_ms
            result["error_type"] = "timeout"
            result["timeout"] = True
            return result
            
        except requests.exceptions.ConnectionError:
            end_time = time.monotonic()
            latency_ms = (end_time - start_time) * 1000.0
            result["latency_ms"] = latency_ms
            result["error_type"] = "connection_error"
            return result
            
        except Exception as e:
            end_time = time.monotonic()
            latency_ms = (end_time - start_time) * 1000.0
            result["latency_ms"] = latency_ms
            result["error_type"] = f"exception_{type(e).__name__}"
            return result
    
    def _worker(self, worker_id: int, end_time: float):
        """工作线程函数"""
        request_id_base = worker_id * 1_000_000
        request_id = request_id_base
        
        while time.monotonic() < end_time:
            request_id += 1
            result = self._make_request(request_id)
            
            with self.lock:
                if result["success"]:
                    self.success_count += 1
                    if result["latency_ms"] is not None:
                        self.success_latencies.append(result["latency_ms"])
                else:
                    self.error_count += 1
                    if result["timeout"]:
                        self.timeout_count += 1
                    
                    # 记录错误类型
                    if result["error_type"]:
                        self.error_details[result["error_type"]] += 1
                    
                    # 记录HTTP状态码
                    if result["http_status"]:
                        self.http_status_codes[result["http_status"]] += 1
                    
                    # 记录RPC错误
                    if result["rpc_error_code"]:
                        self.rpc_errors[result["rpc_error_code"]] += 1
    
    def run(self):
        """运行压力测试"""
        print("=" * 70)
        print("RPC节点压力测试")
        print("=" * 70)
        print(f"目标URL: {self.url}")
        print(f"RPC方法: {self.method}")
        print(f"参数: {self.params}")
        print(f"并发数: {self.concurrency}")
        print(f"持续时间: {self.duration}秒")
        print(f"请求超时: {self.timeout}秒")
        print("=" * 70)
        print("测试进行中...")
        
        start_time = time.monotonic()
        end_time = start_time + self.duration
        
        # 启动工作线程
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = [
                executor.submit(self._worker, i, end_time)
                for i in range(self.concurrency)
            ]
            # 等待所有线程完成
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"工作线程异常: {e}")
        
        elapsed_time = time.monotonic() - start_time
        
        # 生成报告
        self._print_report(elapsed_time)
    
    def _calculate_percentile(self, sorted_values: List[float], percentile: float) -> Optional[float]:
        """计算百分位数"""
        if not sorted_values:
            return None
        k = int(round((percentile / 100.0) * (len(sorted_values) - 1)))
        k = max(0, min(k, len(sorted_values) - 1))
        return sorted_values[k]
    
    def _print_report(self, elapsed_time: float):
        """打印测试报告"""
        total_requests = self.success_count + self.error_count
        success_rate = (self.success_count / total_requests * 100) if total_requests > 0 else 0
        timeout_rate = (self.timeout_count / total_requests * 100) if total_requests > 0 else 0
        error_rate = (self.error_count / total_requests * 100) if total_requests > 0 else 0
        
        qps = total_requests / elapsed_time if elapsed_time > 0 else 0
        success_qps = self.success_count / elapsed_time if elapsed_time > 0 else 0
        
        print("\n" + "=" * 70)
        print("测试结果报告")
        print("=" * 70)
        
        # 总体统计
        print("\n【总体统计】")
        print(f"  测试耗时: {elapsed_time:.2f}秒")
        print(f"  总请求数: {total_requests:,}")
        print(f"  成功请求: {self.success_count:,} ({success_rate:.2f}%)")
        print(f"  错误请求: {self.error_count:,} ({error_rate:.2f}%)")
        print(f"  超时请求: {self.timeout_count:,} ({timeout_rate:.2f}%)")
        print(f"  总QPS: {qps:.2f}")
        print(f"  成功QPS: {success_qps:.2f}")
        
        # 延迟统计
        if self.success_latencies:
            sorted_latencies = sorted(self.success_latencies)
            min_latency = sorted_latencies[0]
            max_latency = sorted_latencies[-1]
            avg_latency = sum(sorted_latencies) / len(sorted_latencies)
            p50 = self._calculate_percentile(sorted_latencies, 50)
            p90 = self._calculate_percentile(sorted_latencies, 90)
            p95 = self._calculate_percentile(sorted_latencies, 95)
            p99 = self._calculate_percentile(sorted_latencies, 99)
            
            print("\n【延迟数据统计】（仅成功请求，单位：毫秒）")
            print(f"  最小值: {min_latency:.2f}ms")
            print(f"  最大值: {max_latency:.2f}ms")
            print(f"  平均值: {avg_latency:.2f}ms")
            print(f"  P50 (中位数): {p50:.2f}ms")
            print(f"  P90: {p90:.2f}ms")
            print(f"  P95: {p95:.2f}ms")
            print(f"  P99: {p99:.2f}ms")
        else:
            print("\n【延迟数据统计】")
            print("  无成功请求，无法统计延迟数据")
        
        # 成功数据统计
        print("\n【成功数据统计】")
        print(f"  成功请求数: {self.success_count:,}")
        print(f"  成功率: {success_rate:.2f}%")
        print(f"  成功QPS: {success_qps:.2f}")
        if self.success_latencies:
            print(f"  平均延迟: {sum(self.success_latencies) / len(self.success_latencies):.2f}ms")
        
        # 超时数据统计
        print("\n【超时数据统计】")
        print(f"  超时请求数: {self.timeout_count:,}")
        print(f"  超时率: {timeout_rate:.2f}%")
        if self.timeout_count > 0:
            timeout_percentage = (self.timeout_count / self.error_count * 100) if self.error_count > 0 else 0
            print(f"  超时占错误比例: {timeout_percentage:.2f}%")
        
        # HTTP状态码统计
        if self.http_status_codes:
            print("\n【HTTP状态码分布】")
            for status, count in self.http_status_codes.most_common():
                percentage = (count / total_requests * 100) if total_requests > 0 else 0
                print(f"  {status}: {count:,} ({percentage:.2f}%)")
        
        # RPC错误统计
        if self.rpc_errors:
            print("\n【RPC错误码分布】")
            for error_code, count in self.rpc_errors.most_common():
                percentage = (count / total_requests * 100) if total_requests > 0 else 0
                print(f"  {error_code}: {count:,} ({percentage:.2f}%)")
        
        # 错误详情统计
        if self.error_details:
            print("\n【错误类型详情】")
            sorted_errors = sorted(self.error_details.items(), key=lambda x: x[1], reverse=True)
            for error_type, count in sorted_errors:
                percentage = (count / total_requests * 100) if total_requests > 0 else 0
                print(f"  {error_type}: {count:,} ({percentage:.2f}%)")
        
        print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="RPC节点压力测试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python test_rpc2.py --url http://localhost:8545 --concurrency 100 --duration 60
  python test_rpc2.py --url http://localhost:8545 --method eth_blockNumber --params "[]" --timeout 5
        """
    )
    
    parser.add_argument(
        "--url",
        required=True,
        help="RPC节点URL，例如: http://localhost:8545"
    )
    parser.add_argument(
        "--method",
        default="eth_blockNumber",
        help="JSON-RPC方法名（默认: eth_blockNumber）"
    )
    parser.add_argument(
        "--params",
        default="[]",
        help='JSON-RPC参数（JSON数组字符串，默认: "[]"）'
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=100,
        help="并发线程数（默认: 100）"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="测试持续时间（秒，默认: 60）"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="单个请求超时时间（秒，默认: 5.0）"
    )
    
    args = parser.parse_args()
    
    # 解析参数
    try:
        params = json.loads(args.params)
        if not isinstance(params, list):
            raise ValueError("params必须是JSON数组")
    except json.JSONDecodeError as e:
        print(f"错误: 无效的JSON参数格式: {e}")
        return 1
    except ValueError as e:
        print(f"错误: {e}")
        return 1
    
    # 运行测试
    try:
        test = RPCStressTest(
            url=args.url,
            method=args.method,
            params=params,
            timeout=args.timeout,
            concurrency=args.concurrency,
            duration=args.duration
        )
        test.run()
        return 0
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
        return 1
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())

