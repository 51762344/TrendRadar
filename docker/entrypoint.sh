#!/bin/bash
set -e

generate_custom_ui() {
    if [ "${CUSTOM_WEB_UI_ENABLED:-false}" != "true" ]; then
        return 0
    fi

    if [ ! -f "/app/cus_files/cus_web_ui/build_site.py" ]; then
        echo "⚠️ 自定义 UI 生成脚本不存在，跳过"
        return 0
    fi

    echo "🎨 生成自定义静态首页..."
    python /app/cus_files/cus_web_ui/build_site.py \
        --project-root /app \
        --output-dir "${CUSTOM_WEB_UI_OUTPUT_DIR:-output}" \
        --title "${CUSTOM_WEB_UI_TITLE:-Engineer News Radar}" || \
        echo "⚠️ 自定义 UI 生成失败，保留官方页面"
}

api_requested() {
    [ "${TRENDRADAR_API_ENABLED:-false}" = "true" ] || [ -n "${TRENDRADAR_API_PORT:-}" ]
}

start_rest_api_background() {
    API_HOST="${TRENDRADAR_API_HOST:-0.0.0.0}"
    API_PORT="${TRENDRADAR_API_PORT:-3334}"

    echo "📰 启动 TrendRadar REST API: ${API_HOST}:${API_PORT}"
    python -m trendradar.api --host "$API_HOST" --port "$API_PORT" &
    API_PID=$!
    sleep 1

    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "❌ TrendRadar REST API 启动失败"
        exit 1
    fi
}

exec_rest_api() {
    API_HOST="${TRENDRADAR_API_HOST:-0.0.0.0}"
    API_PORT="${TRENDRADAR_API_PORT:-${WEBSERVER_PORT:-8080}}"

    echo "📰 启动 TrendRadar REST API: ${API_HOST}:${API_PORT}"
    exec python -m trendradar.api --host "$API_HOST" --port "$API_PORT"
}

# 检查配置文件
if [ ! -f "/app/config/config.yaml" ] || [ ! -f "/app/config/frequency_words.txt" ]; then
    echo "❌ 配置文件缺失"
    exit 1
fi

case "${RUN_MODE:-cron}" in
"api")
    exec_rest_api
    ;;
"once")
    echo "🔄 单次执行"
    exec python -m trendradar
    ;;
"cron")
    # 校验 CRON_SCHEDULE 格式（仅允许 cron 表达式合法字符）
    CRON_EXPR="${CRON_SCHEDULE:-*/30 * * * *}"
    if ! echo "$CRON_EXPR" | grep -qE '^[0-9*/,[:space:]-]+$'; then
        echo "❌ CRON_SCHEDULE 格式非法: $CRON_EXPR"
        exit 1
    fi

    # 生成 crontab
    if [ "${CUSTOM_WEB_UI_ENABLED:-false}" = "true" ]; then
        echo "$CRON_EXPR cd /app && python -m trendradar && python /app/cus_files/cus_web_ui/build_site.py --project-root /app --output-dir \"\${CUSTOM_WEB_UI_OUTPUT_DIR:-output}\" --title \"\${CUSTOM_WEB_UI_TITLE:-Engineer News Radar}\"" > /tmp/crontab
    else
        echo "$CRON_EXPR cd /app && python -m trendradar" > /tmp/crontab
    fi
    
    echo "📅 生成的crontab内容:"
    cat /tmp/crontab

    if ! /usr/local/bin/supercronic -test /tmp/crontab; then
        echo "❌ crontab格式验证失败"
        exit 1
    fi

    # 立即执行一次（如果配置了）
    if [ "${IMMEDIATE_RUN:-false}" = "true" ]; then
        echo "▶️ 立即执行一次"
        python -m trendradar
        generate_custom_ui
    fi

    if api_requested; then
        API_PORT="${TRENDRADAR_API_PORT:-3334}"
        if [ "$API_PORT" = "${WEBSERVER_PORT:-8080}" ]; then
            echo "📰 REST API 使用 ${API_PORT} 端口，跳过静态 Web 服务器以避免端口冲突"
        else
            echo "🌐 启动 Web 服务器..."
            python manage.py start_webserver
        fi

        start_rest_api_background
    else
        # 启动 Web 服务器
        echo "🌐 启动 Web 服务器..."
        python manage.py start_webserver
    fi

    echo "⏰ 启动supercronic: $CRON_EXPR"
    echo "🎯 supercronic 将作为 PID 1 运行"

    exec /usr/local/bin/supercronic -passthrough-logs /tmp/crontab
    ;;
*)
    exec "$@"
    ;;
esac
