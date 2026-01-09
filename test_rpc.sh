#!/usr/bin/env bash
set -euo pipefail

# -----------------------------
# JSON-RPC 压测脚本（Bash 版本，带实时打印）
# 统计指标：
# - 成功/超时/HTTP 错误/JSON-RPC 错误/curl 错误
# - 延迟统计：平均值、P50/P90/P95/P99、最大值
# - 实时打印：每秒输出 QPS、成功率、超时/错误、近1秒平均延迟
#
# 依赖：curl、awk、sort、date、wc、tail、grep
# 可选：jq（更准确判断 JSON-RPC 是否成功/是否返回 error）
# -----------------------------

URL=""
CONCURRENCY=100
DURATION=30
METHOD=""
PARAMS="[]"
CONNECT_TIMEOUT=2
REQUEST_TIMEOUT=10
INSECURE=0

# 实时打印是否计算“累计分位数”（每秒会 sort 全量延迟，样本非常大时可能较重）
REALTIME_WITH_PERCENTILE=1

usage() {
  cat <<EOF
用法：
  $0 --url <URL> --concurrency <并发数> --duration <持续秒数> --method <rpc方法> --params '<json参数>' [可选项]

必填参数：
  --url            JSON-RPC 地址，例如：http://127.0.0.1:8545
  --concurrency    并发 worker 数（并行子进程数）
  --duration       压测持续时间（秒）
  --method         JSON-RPC 方法名，例如：eth_getLogs
  --params         JSON 字符串，作为 params 字段，例如：'[{"address":"0x..."}]'

可选参数：
  --connect-timeout <秒>    curl 连接超时（默认：2）
  --timeout <秒>            curl 单请求最大耗时（默认：10）
  -k, --insecure            HTTPS 忽略证书校验（如果是 https）
  --realtime-percentile 0|1 实时打印是否计算累计分位数（默认：1）
  -h, --help                显示帮助

示例：
  $0 --url http://104.233.194.10:8545 --concurrency 20000 --duration 300 \\
     --method eth_getLogs --params '[{"address":"0x9244212403a2e827cadca1f6fb68b43bc0c7a41f"}]' \\
     --timeout 10 --connect-timeout 2
EOF
}

# --------- 解析命令行参数 ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2;;
    --concurrency) CONCURRENCY="$2"; shift 2;;
    --duration) DURATION="$2"; shift 2;;
    --method) METHOD="$2"; shift 2;;
    --params) PARAMS="$2"; shift 2;;
    --connect-timeout) CONNECT_TIMEOUT="$2"; shift 2;;
    --timeout) REQUEST_TIMEOUT="$2"; shift 2;;
    --realtime-percentile) REALTIME_WITH_PERCENTILE="$2"; shift 2;;
    -k|--insecure) INSECURE=1; shift 1;;
    -h|--help) usage; exit 0;;
    *) echo "未知参数：$1"; usage; exit 1;;
  esac
done

if [[ -z "${URL}" || -z "${METHOD}" ]]; then
  echo "缺少必填参数（--url 或 --method）"
  usage
  exit 1
fi

# --------- 环境检查 ----------
command -v curl >/dev/null || { echo "缺少依赖：curl"; exit 1; }
command -v awk  >/dev/null || { echo "缺少依赖：awk"; exit 1; }
command -v sort >/dev/null || { echo "缺少依赖：sort"; exit 1; }
command -v date >/dev/null || { echo "缺少依赖：date"; exit 1; }
command -v wc   >/dev/null || { echo "缺少依赖：wc"; exit 1; }
command -v tail >/dev/null || { echo "缺少依赖：tail"; exit 1; }
command -v grep >/dev/null || { echo "缺少依赖：grep"; exit 1; }

HAS_JQ=0
if command -v jq >/dev/null; then HAS_JQ=1; fi

# 尝试提升文件句柄上限（高并发时需要）
ulimit -n 1000000 >/dev/null 2>&1 || true

# 创建临时工作目录
WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

LAT_FILE="${WORKDIR}/lat_ms.txt"       # 每次请求延迟（毫秒）
MET_FILE="${WORKDIR}/metrics.txt"      # 指标事件（total/success/timeout/http_err/rpc_err/...）

: > "${LAT_FILE}"
: > "${MET_FILE}"

START_EPOCH="$(date +%s)"
END_EPOCH="$((START_EPOCH + DURATION))"

TLS_FLAG=""
if [[ "${INSECURE}" -eq 1 ]]; then TLS_FLAG="-k"; fi

# -----------------------------------
# 发起一次 JSON-RPC 请求
# 输出一行：<ms> <http_code> <ok> <rpc_err>
# 返回码：
#  - 0 表示 curl 正常完成（不代表 RPC 一定成功）
#  - 28 表示 curl 超时
#  - 其它表示 curl 失败
# -----------------------------------
do_one() {
  local id="$1"
  local payload
  payload=$(printf '{"jsonrpc":"2.0","id":%s,"method":"%s","params":%s}' "$id" "$METHOD" "$PARAMS")

  local tmp_body="${WORKDIR}/body_${$}_${RANDOM}.txt"
  local out

  out=$(curl -sS ${TLS_FLAG} \
      --connect-timeout "${CONNECT_TIMEOUT}" \
      --max-time "${REQUEST_TIMEOUT}" \
      -H 'Content-Type: application/json' \
      -d "${payload}" \
      -o "${tmp_body}" \
      -w "HTTP=%{http_code} TIME=%{time_total} ERR=%{errormsg}\n" \
      "${URL}" 2>/dev/null) || return $?

  local http_code time_total
  http_code="$(echo "${out}" | awk '{for(i=1;i<=NF;i++){if($i ~ /^HTTP=/){sub("HTTP=","",$i); print $i}}}')"
  time_total="$(echo "${out}" | awk '{for(i=1;i<=NF;i++){if($i ~ /^TIME=/){sub("TIME=","",$i); print $i}}}')"

  local ms
  ms=$(awk -v t="${time_total}" 'BEGIN{printf("%d", t*1000)}')

  local ok=0 rpc_err=0
  if [[ "${http_code}" == "200" ]]; then
    if [[ "${HAS_JQ}" -eq 1 ]]; then
      if jq -e 'has("error") and .error != null' "${tmp_body}" >/dev/null 2>&1; then
        rpc_err=1
      elif jq -e 'has("result")' "${tmp_body}" >/dev/null 2>&1; then
        ok=1
      else
        rpc_err=1
      fi
    else
      if grep -q '"result"' "${tmp_body}" && ! grep -q '"error"' "${tmp_body}"; then
        ok=1
      else
        rpc_err=1
      fi
    fi
  fi

  rm -f "${tmp_body}" >/dev/null 2>&1 || true

  echo "${ms} ${http_code} ${ok} ${rpc_err}"
  return 0
}

# -----------------------------------
# worker：持续循环发请求，直到到达结束时间
# -----------------------------------
worker() {
  local wid="$1"
  local rid=0

  while :; do
    local now
    now="$(date +%s)"
    if [[ "${now}" -ge "${END_EPOCH}" ]]; then
      break
    fi

    rid=$((rid+1))

    set +e
    local line
    line="$(do_one "${rid}")"
    local rc=$?
    set -e

    if [[ $rc -eq 0 ]]; then
      local ms http ok rpc_err
      ms="$(echo "${line}" | awk '{print $1}')"
      http="$(echo "${line}" | awk '{print $2}')"
      ok="$(echo "${line}" | awk '{print $3}')"
      rpc_err="$(echo "${line}" | awk '{print $4}')"

      echo "${ms}" >> "${LAT_FILE}"

      if [[ "${ok}" -eq 1 ]]; then
        echo "success" >> "${MET_FILE}"
      else
        if [[ "${http}" != "200" ]]; then
          echo "http_err" >> "${MET_FILE}"
        elif [[ "${rpc_err}" -eq 1 ]]; then
          echo "rpc_err" >> "${MET_FILE}"
        else
          echo "other_err" >> "${MET_FILE}"
        fi
      fi
      echo "total" >> "${MET_FILE}"
    else
      echo "total" >> "${MET_FILE}"
      if [[ $rc -eq 28 ]]; then
        echo "timeout" >> "${MET_FILE}"
      else
        echo "curl_err" >> "${MET_FILE}"
      fi
    fi
  done
}

# -----------------------------------
# 统计器：每秒实时打印
# - 增量统计：通过“读取新增行数”来计算过去1秒变化
# - 延迟：近1秒平均延迟（通过新增延迟行计算）
# - 可选：每秒计算累计 P50/P95/P99（可能较重）
# -----------------------------------
realtime_reporter() {
  local last_met_lines=0
  local last_lat_lines=0

  local last_total=0 last_success=0 last_timeout=0 last_http_err=0 last_rpc_err=0 last_curl_err=0 last_other_err=0
  local t0="${START_EPOCH}"

  echo "== 实时统计（每秒一行）=="
  echo "时间(s)  QPS(1s/累计)  成功率(1s/累计)  1s:总/成/超时/HTTP/RPC/curl/其它  延迟ms(1s均值/累计P50/P95/P99)"

  while :; do
    local now
    now="$(date +%s)"
    if [[ "${now}" -ge "${END_EPOCH}" ]]; then
      break
    fi

    sleep 1

    # 读取累计行数
    local met_lines lat_lines
    met_lines=$(wc -l < "${MET_FILE}" 2>/dev/null | tr -d ' ')
    lat_lines=$(wc -l < "${LAT_FILE}" 2>/dev/null | tr -d ' ')

    # 统计累计（通过 grep 计数，简单可靠；每秒一次开销可接受）
    local total success timeout http_err rpc_err curl_err other_err
    total=$(grep -c '^total$'   "${MET_FILE}" 2>/dev/null || echo 0)
    success=$(grep -c '^success$' "${MET_FILE}" 2>/dev/null || echo 0)
    timeout=$(grep -c '^timeout$' "${MET_FILE}" 2>/dev/null || echo 0)
    http_err=$(grep -c '^http_err$' "${MET_FILE}" 2>/dev/null || echo 0)
    rpc_err=$(grep -c '^rpc_err$' "${MET_FILE}" 2>/dev/null || echo 0)
    curl_err=$(grep -c '^curl_err$' "${MET_FILE}" 2>/dev/null || echo 0)
    other_err=$(grep -c '^other_err$' "${MET_FILE}" 2>/dev/null || echo 0)

    # 计算过去1秒增量
    local d_total d_success d_timeout d_http_err d_rpc_err d_curl_err d_other_err
    d_total=$((total - last_total))
    d_success=$((success - last_success))
    d_timeout=$((timeout - last_timeout))
    d_http_err=$((http_err - last_http_err))
    d_rpc_err=$((rpc_err - last_rpc_err))
    d_curl_err=$((curl_err - last_curl_err))
    d_other_err=$((other_err - last_other_err))

    last_total="${total}"
    last_success="${success}"
    last_timeout="${timeout}"
    last_http_err="${http_err}"
    last_rpc_err="${rpc_err}"
    last_curl_err="${curl_err}"
    last_other_err="${other_err}"

    # 近1秒延迟均值：取新增的延迟行
    local d_lat_lines d_lat_avg
    d_lat_lines=$((lat_lines - last_lat_lines))
    d_lat_avg="NA"
    if [[ "${d_lat_lines}" -gt 0 ]]; then
      # 取最后 d_lat_lines 行求平均
      d_lat_avg=$(tail -n "${d_lat_lines}" "${LAT_FILE}" | awk '{sum+=$1} END{if(NR>0) printf("%.2f", sum/NR); else print "NA"}')
    fi
    last_lat_lines="${lat_lines}"

    # 1秒QPS与累计QPS
    local elapsed
    elapsed=$(( (now + 1) - t0 ))   # +1 是因为 sleep 1 后打印
    if [[ "${elapsed}" -le 0 ]]; then elapsed=1; fi
    local qps_1s qps_all
    qps_1s="${d_total}"
    qps_all=$(awk -v t="${total}" -v e="${elapsed}" 'BEGIN{if(e>0) printf("%.2f", t/e); else print "0.00"}')

    # 成功率（1秒/累计）
    local sr_1s sr_all
    sr_1s="NA"
    if [[ "${d_total}" -gt 0 ]]; then
      sr_1s=$(awk -v s="${d_success}" -v t="${d_total}" 'BEGIN{printf("%.2f", (s/t)*100)}')
    fi
    sr_all="NA"
    if [[ "${total}" -gt 0 ]]; then
      sr_all=$(awk -v s="${success}" -v t="${total}" 'BEGIN{printf("%.2f", (s/t)*100)}')
    fi

    # 可选：累计分位数（每秒 sort 全量延迟）
    local p50="NA" p95="NA" p99="NA"
    if [[ "${REALTIME_WITH_PERCENTILE}" -eq 1 && "${lat_lines}" -gt 0 ]]; then
      local lat_sorted="${WORKDIR}/lat_sorted_rt.txt"
      grep -E '^[0-9]+$' "${LAT_FILE}" | sort -n > "${lat_sorted}" 2>/dev/null || true

      perc() {
        local p="$1"
        awk -v P="${p}" '
          {a[NR]=$1}
          END{
            if(NR==0){print "NA"; exit}
            k=int((P/100.0)*NR + 0.999999);
            if(k<1)k=1; if(k>NR)k=NR;
            print a[k]
          }' "${lat_sorted}"
      }

      p50=$(perc 50)
      p95=$(perc 95)
      p99=$(perc 99)
    fi

    local sec
    sec=$(( (now + 1) - t0 ))

    printf "%7s  %8s/%7s  %7s%%/%7s%%  %s/%s/%s/%s/%s/%s/%s  %s/%s/%s/%s\n" \
      "${sec}" \
      "${qps_1s}" "${qps_all}" \
      "${sr_1s}" "${sr_all}" \
      "${d_total}" "${d_success}" "${d_timeout}" "${d_http_err}" "${d_rpc_err}" "${d_curl_err}" "${d_other_err}" \
      "${d_lat_avg}" "${p50}" "${p95}" "${p99}"
  done
}

# -----------------------------------
# 运行信息输出
# -----------------------------------
echo "== JSON-RPC 压测开始 =="
echo "目标 URL：             ${URL}"
echo "RPC 方法：             ${METHOD}"
echo "RPC 参数 params：      ${PARAMS}"
echo "并发 worker 数：       ${CONCURRENCY}"
echo "压测时长(秒)：         ${DURATION}"
echo "连接超时(秒)：         ${CONNECT_TIMEOUT}"
echo "单请求超时(秒)：       ${REQUEST_TIMEOUT}"
echo "是否安装 jq：          $([[ ${HAS_JQ} -eq 1 ]] && echo 是 || echo 否)"
echo "实时分位数(每秒)：     $([[ ${REALTIME_WITH_PERCENTILE} -eq 1 ]] && echo 开启 || echo 关闭)"
echo "临时目录：             ${WORKDIR}"
echo

# 先启动实时统计器（后台）
(realtime_reporter) &
REPORTER_PID="$!"

# 启动并发 worker
pids=()
for i in $(seq 1 "${CONCURRENCY}"); do
  ( worker "$i" ) &
  pids+=("$!")
done

# 等待所有 worker 退出
for p in "${pids[@]}"; do
  wait "$p" || true
done

# 等待统计器结束
wait "${REPORTER_PID}" || true

# -----------------------------------
# 汇总统计
# -----------------------------------
total=$(grep -c '^total$'   "${MET_FILE}" 2>/dev/null || echo 0)
success=$(grep -c '^success$' "${MET_FILE}" 2>/dev/null || echo 0)
timeout=$(grep -c '^timeout$' "${MET_FILE}" 2>/dev/null || echo 0)
http_err=$(grep -c '^http_err$' "${MET_FILE}" 2>/dev/null || echo 0)
rpc_err=$(grep -c '^rpc_err$' "${MET_FILE}" 2>/dev/null || echo 0)
curl_err=$(grep -c '^curl_err$' "${MET_FILE}" 2>/dev/null || echo 0)
other_err=$(grep -c '^other_err$' "${MET_FILE}" 2>/dev/null || echo 0)

succ_rate="0.00"
if [[ "${total}" -gt 0 ]]; then
  succ_rate=$(awk -v s="${success}" -v t="${total}" 'BEGIN{printf("%.2f", (s/t)*100)}')
fi

# 延迟统计：排序后计算平均值、分位数
LAT_SORTED="${WORKDIR}/lat_sorted.txt"
grep -E '^[0-9]+$' "${LAT_FILE}" | sort -n > "${LAT_SORTED}" || true
count_lat=$(wc -l < "${LAT_SORTED}" | tr -d ' ')

avg="NA"; p50="NA"; p90="NA"; p95="NA"; p99="NA"; maxv="NA"
if [[ "${count_lat}" -gt 0 ]]; then
  avg=$(awk '{sum+=$1} END{printf("%.2f", sum/NR)}' "${LAT_SORTED}")
  maxv=$(tail -n 1 "${LAT_SORTED}")

  perc() {
    local p="$1"
    awk -v P="${p}" '
      {a[NR]=$1}
      END{
        if(NR==0){print "NA"; exit}
        k=int((P/100.0)*NR + 0.999999);
        if(k<1)k=1; if(k>NR)k=NR;
        print a[k]
      }' "${LAT_SORTED}"
  }

  p50=$(perc 50)
  p90=$(perc 90)
  p95=$(perc 95)
  p99=$(perc 99)
fi

# 输出最终结果
echo
echo "== 压测结果汇总 =="
echo "总请求数：             ${total}"
echo "成功数：               ${success}"
echo "成功率：               ${succ_rate}%"
echo "超时数：               ${timeout}"
echo "HTTP 非 200 数：       ${http_err}"
echo "JSON-RPC 错误数：      ${rpc_err}"
echo "curl 错误数：          ${curl_err}"
echo "其它错误数：           ${other_err}"
echo
echo "== 延迟统计（毫秒 ms）=="
echo "样本数：               ${count_lat}"
echo "平均延迟：             ${avg}"
echo "P50：                  ${p50}"
echo "P90：                  ${p90}"
echo "P95：                  ${p95}"
echo "P99：                  ${p99}"
echo "最大延迟：             ${maxv}"
