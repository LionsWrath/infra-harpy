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

# Link: https://docs.ntfy.sh/publish/#tags-emojis

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/../vars.sh

notify () {
    docker exec nginx-proxy-manager curl -s \
        -H "Title: $1" \
        -H "Priority: $2" \
        -H "Tags: $3" \
        -d "$4" \
        $5
}