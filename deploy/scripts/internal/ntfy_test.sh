# Priorities:
#  5  max / urgent
#  4  high
#  3  default
#  2  low
#  1  min

# Tags: (not all)
#  tada
#  heavy_check_mark
#  warning
#  rotating_light
#  computer

notify () {
    docker exec nginx-proxy-manager curl \
        -H "Title: Test Notification" \
        -H "Priority: $1" \
        -H "Tags: $2" \
        -d $3 \
        $4
}

notify "default" "computer" "This is a test notification." ${MANAGE_TOPIC}

