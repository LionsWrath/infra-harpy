# core.telegram.org/bots/api#sendmessage

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/../vars.sh

notify () {

    case "$3" in
    ok)
        EMOJI="✔️"
        ;;
    warning)
        EMOJI="⚠️"
        ;;
    danger)
        EMOJI="❌"
        ;;
    *)
        EMOJI="❓"
        ;;
    esac

    case "$2" in
    high)
        NOTIF=false
        ;;
    *)
        NOTIF=true
        ;;
    esac

    case "$5" in
    media)
        TOPIC="3"
        ;;
    infra)
        TOPIC="52"
        ;;
    *)
        TOPIC="1"
        ;;
    esac

    docker exec nginx-proxy-manager curl -X POST \
	    -H "Content-Type: application/json" \
        -d "{
                \"chat_id\": \"${TELEGRAM_CHAT_ID}\",
                \"text\": \"${EMOJI} $1 \n $4\",
                \"disable_notification\": ${NOTIF},
                \"parse_type\": true,
                \"message_thread_id\": \"${TOPIC}\"
            }" \
        https://api.telegram.org/bot${TELEGRAM_BOT_KEY}/sendMessage
}
