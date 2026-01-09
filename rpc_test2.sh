#!/bin/bash

# RPC 压测脚本
# 使用方法: ./rpc_test2.sh [并发数] [总请求数]

# 默认配置
CONCURRENT=${1:-10}      # 并发数，默认10
TOTAL_REQUESTS=${2:-100} # 总请求数，默认100
RPC_URL="http://104.233.194.10:8545"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 统计变量
SUCCESS_COUNT=0
FAIL_COUNT=0
TOTAL_TIME=0
START_TIME=$(date +%s.%N)

# 临时文件存储结果
RESULT_DIR=$(mktemp -d)
trap "rm -rf $RESULT_DIR" EXIT

echo -e "${YELLOW}开始压测...${NC}"
echo "RPC URL: $RPC_URL"
echo "并发数: $CONCURRENT"
echo "总请求数: $TOTAL_REQUESTS"
echo "----------------------------------------"

# 单个请求函数
make_request() {
    local request_id=$1
    local start=$(date +%s.%N)
    
    response=$(curl -sS -w "\n%{http_code}\n%{time_total}" \
        -H 'Content-Type: application/json' \
        --data '{"jsonrpc":"2.0","id":1,"method":"eth_getLogs","params":[{"fromBlock":"0x04f83780","toBlock":"0x04f83cdb","address":"0x9244212403a2e827cadca1f6fb68b43bc0c7a41f"}]}' \
        "$RPC_URL" 2>&1)
    
    local end=$(date +%s.%N)
    local duration=$(echo "$end - $start" | bc)
    
    # 解析响应
    local http_code=$(echo "$response" | tail -n 2 | head -n 1)
    local curl_time=$(echo "$response" | tail -n 1)
    
    # 检查是否成功（HTTP 200 且响应包含 jsonrpc）
    if [[ "$http_code" == "200" ]] && echo "$response" | grep -q "jsonrpc"; then
        echo "SUCCESS $duration" > "$RESULT_DIR/result_$request_id"
    else
        echo "FAIL $duration" > "$RESULT_DIR/result_$request_id"
    fi
}

# 使用 xargs 进行并发控制
export -f make_request
export RPC_URL RESULT_DIR

seq 1 $TOTAL_REQUESTS | xargs -n 1 -P $CONCURRENT -I {} bash -c 'make_request {}'

# 统计结果并收集延迟数据
LATENCY_FILE="$RESULT_DIR/latencies.txt"
touch "$LATENCY_FILE"

for result_file in "$RESULT_DIR"/result_*; do
    if [ -f "$result_file" ]; then
        result=$(cat "$result_file")
        if [[ "$result" == SUCCESS* ]]; then
            ((SUCCESS_COUNT++))
            time=$(echo "$result" | awk '{print $2}')
            TOTAL_TIME=$(echo "$TOTAL_TIME + $time" | bc)
            echo "$time" >> "$LATENCY_FILE"
        else
            ((FAIL_COUNT++))
        fi
    fi
done

END_TIME=$(date +%s.%N)
ELAPSED=$(echo "$END_TIME - $START_TIME" | bc)

# 计算平均响应时间（毫秒）
if [ $SUCCESS_COUNT -gt 0 ]; then
    AVG_TIME_MS=$(echo "scale=2; ($TOTAL_TIME / $SUCCESS_COUNT) * 1000" | bc)
else
    AVG_TIME_MS=0
fi

# 计算 QPS
QPS=$(echo "scale=2; $TOTAL_REQUESTS / $ELAPSED" | bc)

# 计算延迟统计（最小、最大、中位数、百分位数）- 单位：毫秒
if [ $SUCCESS_COUNT -gt 0 ] && [ -s "$LATENCY_FILE" ]; then
    # 对延迟时间排序
    SORTED_LATENCIES=$(sort -n "$LATENCY_FILE")
    
    # 最小延迟（转换为毫秒）
    MIN_LATENCY_SEC=$(echo "$SORTED_LATENCIES" | head -n 1)
    MIN_LATENCY=$(echo "scale=2; $MIN_LATENCY_SEC * 1000" | bc)
    
    # 最大延迟（转换为毫秒）
    MAX_LATENCY_SEC=$(echo "$SORTED_LATENCIES" | tail -n 1)
    MAX_LATENCY=$(echo "scale=2; $MAX_LATENCY_SEC * 1000" | bc)
    
    # 计算百分位数函数
    get_percentile() {
        local percentile=$1
        local count=$SUCCESS_COUNT
        # 使用标准百分位数公式: index = (count - 1) * percentile / 100 + 1
        local index=$(echo "scale=0; (($count - 1) * $percentile / 100) + 1" | bc | cut -d. -f1)
        if [ "$index" -lt 1 ]; then
            index=1
        fi
        if [ "$index" -gt "$count" ]; then
            index=$count
        fi
        local latency_sec=$(echo "$SORTED_LATENCIES" | sed -n "${index}p")
        echo "scale=2; $latency_sec * 1000" | bc
    }
    
    # 中位数 (P50)
    P50=$(get_percentile 50)
    
    # P90
    P90=$(get_percentile 90)
    
    # P95
    P95=$(get_percentile 95)
    
    # P99
    P99=$(get_percentile 99)
else
    MIN_LATENCY=0
    MAX_LATENCY=0
    P50=0
    P90=0
    P95=0
    P99=0
fi

# 输出结果
echo "----------------------------------------"
echo -e "${GREEN}压测完成！${NC}"
echo "总请求数: $TOTAL_REQUESTS"
echo -e "${GREEN}成功: $SUCCESS_COUNT${NC}"
echo -e "${RED}失败: $FAIL_COUNT${NC}"
echo "总耗时: ${ELAPSED}s"
echo "QPS: $QPS"
echo ""
echo -e "${YELLOW}=== 延迟统计 (毫秒) ===${NC}"
echo "平均延迟: ${AVG_TIME_MS}ms"
echo "最小延迟: ${MIN_LATENCY}ms"
echo "最大延迟: ${MAX_LATENCY}ms"
echo "中位数 (P50): ${P50}ms"
echo "P90: ${P90}ms"
echo "P95: ${P95}ms"
echo "P99: ${P99}ms"

