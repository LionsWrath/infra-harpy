
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source ${SCRIPT_DIR}/ntfy.sh

notify "Test Notification" "default" "computer" "This is a test notification." ${MANAGE_TOPIC}

