#!/bin/bash

#==============================================================================
# Configuration
#==============================================================================
# Define output location
OUTPUT_DIR="/path/to/appdata"
OUTPUT_FILE="$OUTPUT_DIR/stats.json"
CACHE_DIR="/tmp/homepage_exporter"
CACHE_FILE="$CACHE_DIR/dir_stats.cache"

# API endpoints and tokens
JELLYSTAT_API="https://jellystat.orochi.chst.dev/stats"
API_TOKEN="[REDACTED-KEY]"
IMMICH_API="https://photos.orochi.chst.dev/api/server/statistics"
IMMICH_API_KEY="[REDACTED-KEY]"

#==============================================================================
# Directory 
#==============================================================================
# Ensure output and cache directories exist
mkdir -p "$OUTPUT_DIR"
mkdir -p "$CACHE_DIR"

#==============================================================================
# Formatting Functions
#==============================================================================
# Convert bytes to human-readable GB/TB format
format_size() {
    local bytes=$1
    local gb=$(echo "scale=2; $bytes / 1024 / 1024 / 1024" | bc)
    local tb=$(echo "scale=2; $gb / 1024" | bc)
    
    if (( $(echo "$tb >= 1" | bc -l) )); then
        printf "%.2f TB" $tb
    else
        printf "%.2f GB" $gb
    fi
}

# Add thousand separators to numbers
format_number() {
    printf "%'d" $1
}

# Format user storage stats with separator
format_user_stats() {
    local size=$1
    local count=$2
    local type=$3
    printf "%s\\\\n|\\\\n%'d %s\\\\n_" "$(format_size $size)" $count $type
}

# Format stats without separator
format_stats() {
    local size=$1
    local count=$2
    local type=$3
    printf "%s\\\\n%'d %s" "$(format_size $size)" $count $type
}

# Format stats in combined format
format_combined_stats() {
    local size=$1
    local count=$2
    local type=$3
    printf "%s - %'d %s" "$(format_size $size)" $count $type
}

#==============================================================================
# Time Formatting Functions
#==============================================================================
# Convert seconds to hours for user watch time
format_watch_time() {
    local seconds=${1:-0}
    if [ -z "$seconds" ] || [ "$seconds" = "null" ]; then
        echo "0 h"
        return
    fi
    
    local hours=$(( ($seconds + 1800) / 3600 ))
    printf "%'d h" $hours
}

# Convert microseconds to hours for media duration
format_duration() {
    local microseconds=${1:-0}
    if [ -z "$microseconds" ] || [ "$microseconds" = "null" ]; then
        echo "0 h"
        return
    fi
    
    local hours=$(( ($microseconds + 18000000000) / 36000000000 ))
    printf "%'d h" $hours
}

#==============================================================================
# Directory Statistics Functions
#==============================================================================
# Get total size of directory in bytes
get_dir_size() {
    du -sb "$1" 2>/dev/null | cut -f1
}

# Get size of clips directory
get_clips_size() {
    du -sb "$1" 2>/dev/null | cut -f1
}

# Count files in clips directory
get_clips_files() {
    find "$1" -type f | wc -l
}

# Count game folders
get_game_count() {
    local count=$(($(find "$1" -maxdepth 1 -type d | wc -l) - 1))
    echo "$count"
}

#==============================================================================
# Media Analysis Functions
#==============================================================================
# Calculate total runtime of video files with caching
get_clips_runtime() {
    local dir="$1"
    local cache_file="/tmp/homepage_exporter/video_runtime.cache"
    local total_seconds=0
    
    # Cache setup
    mkdir -p "/tmp/homepage_exporter"
    local current_mtime=$(stat -c %Y "$dir" 2>/dev/null)
    
    # Check cache validity
    if [ -f "$cache_file" ] && cached_line=$(grep "^$dir:" "$cache_file" 2>/dev/null); then
        cached_mtime=$(echo "$cached_line" | cut -d: -f2)
        if [ "$current_mtime" = "$cached_mtime" ]; then
            total_seconds=$(echo "$cached_line" | cut -d: -f3)
        fi
    fi
    
    # Recalculate if cache invalid
    if [ -z "$total_seconds" ] || [ "$total_seconds" = "0" ]; then
        while IFS= read -r -d '' file; do
            duration=$(mediainfo --Inform="General;%Duration%" "$file" 2>/dev/null)
            if [ ! -z "$duration" ] && [ "$duration" != "0" ]; then
                secs=$(echo "scale=3; $duration/1000" | bc)
                total_seconds=$(echo "$total_seconds + $secs" | bc)
            fi
        done < <(find "$dir" -type f \( -name "*.mp4" -o -name "*.mkv" -o -name "*.mov" \) -print0)
        
        # Update cache
        if [ "$total_seconds" != "0" ]; then
            echo "$dir:$current_mtime:$total_seconds" > "$cache_file.tmp"
            mv "$cache_file.tmp" "$cache_file"
        fi
    fi

    # Convert to hours and format
    hours=$(echo "scale=2; $total_seconds/3600" | bc)
    printf "%.2f h" $hours
}

#==============================================================================
# API Functions
#==============================================================================
# Fetch data from Jellystat API
get_jellystat_data() {
    local endpoint=$1
    local method=${2:-GET}  # Default to GET if no method specified
    
    if [ "$method" = "POST" ]; then
        curl -s -X POST -H "x-api-token: $API_TOKEN" "$JELLYSTAT_API/$endpoint"
    else
        curl -s -H "x-api-token: $API_TOKEN" "$JELLYSTAT_API/$endpoint"
    fi
}

# Fetch statistics from Immich API
get_immich_stats() {
    curl -s -H "x-api-key: $IMMICH_API_KEY" "$IMMICH_API"
}

#==============================================================================
# Main Script Execution
#==============================================================================
# Set locale for number formatting
export LC_NUMERIC="en_US.UTF-8"

#==============================================================================
# API Data Collection
#==============================================================================
# Fetch Jellystat API data
echo "Fetching Jellystat data..."
CARD_STATS=$(get_jellystat_data "getLibraryCardStats" "GET")
METADATA=$(get_jellystat_data "getLibraryMetadata" "GET")
USER_ACTIVITY=$(get_jellystat_data "getAllUserActivity" "GET")
MOST_USED_CLIENT=$(get_jellystat_data "getMostUsedClient" "POST")

# Fetch Immich API data
echo "Fetching Immich data..."
IMMICH_DATA=$(get_immich_stats)

#==============================================================================
# Data Processing - Jellystat
#==============================================================================
# Extract movies and shows data
MOVIES_DATA=$(echo "$CARD_STATS" | jq -r '.[] | select(.Name=="Movies")')
SHOWS_DATA=$(echo "$CARD_STATS" | jq -r '.[] | select(.Name=="Shows")')
MOVIES_META=$(echo "$METADATA" | jq -r '.[] | select(.Name=="Movies")')
SHOWS_META=$(echo "$METADATA" | jq -r '.[] | select(.Name=="Shows")')

# Extract music library data by user
GARNET_MUSIC=$(echo "$METADATA" | jq -r '.[] | select(.Id=="[REDACTED-LIB-ID]")')
RASTA_MUSIC=$(echo "$METADATA" | jq -r '.[] | select(.Id=="[REDACTED-LIB-ID]")')
CHE_MUSIC=$(echo "$METADATA" | jq -r '.[] | select(.Id=="[REDACTED-LIB-ID]")')
GRAHAM_MUSIC=$(echo "$METADATA" | jq -r '.[] | select(.Id=="[REDACTED-LIB-ID]")')

# Calculate combined music statistics
COMBINED_SIZE=$(echo "$METADATA" | jq -r '[.[] | select(.Id | IN("[REDACTED-LIB-ID]", "[REDACTED-LIB-ID]", "[REDACTED-LIB-ID]", "[REDACTED-LIB-ID]")) | .Size | tonumber] | add')
COMBINED_FILES=$(echo "$METADATA" | jq -r '[.[] | select(.Id | IN("[REDACTED-LIB-ID]", "[REDACTED-LIB-ID]", "[REDACTED-LIB-ID]", "[REDACTED-LIB-ID]")) | .files | tonumber] | add')

#==============================================================================
# Data Processing - Movies and Shows
#==============================================================================
# Extract movie statistics with default values
MOVIES_RUNTIME=$(echo "$MOVIES_DATA" | jq -r '.total_play_time // 0')
MOVIES_PLAYS=$(echo "$MOVIES_DATA" | jq -r '.Plays // 0')
MOVIES_STORAGE=$(echo "$MOVIES_META" | jq -r '.Size // 0')
MOVIES_COUNT=$(echo "$MOVIES_DATA" | jq -r '.Library_Count // 0')

# Extract show statistics with default values
SHOWS_RUNTIME=$(echo "$SHOWS_DATA" | jq -r '.total_play_time // 0')
SHOWS_PLAYS=$(echo "$SHOWS_DATA" | jq -r '.Plays // 0')
SHOWS_STORAGE=$(echo "$SHOWS_META" | jq -r '.Size // 0')
SHOWS_EPISODES=$(echo "$SHOWS_DATA" | jq -r '.Episode_Count // 0')

# Add these two lines to format the storage values
MOVIES_STORAGE_FMT=$(format_size "$MOVIES_STORAGE")
SHOWS_STORAGE_FMT=$(format_size "$SHOWS_STORAGE")

# Format runtime statistics
MOVIES_RUNTIME_FMT=$(format_duration "$MOVIES_RUNTIME")
SHOWS_RUNTIME_FMT=$(format_duration "$SHOWS_RUNTIME")

#==============================================================================
# Data Processing - Client Usage
#==============================================================================
# Extract and format client names
CLIENT_1=$(echo "$MOST_USED_CLIENT" | jq -r '.[0].Client' | sed 's/Discord Jellyfin Music Bot/SiennaTunes/')
CLIENT_2=$(echo "$MOST_USED_CLIENT" | jq -r '.[1].Client' | sed 's/Discord Jellyfin Music Bot/SiennaTunes/')
CLIENT_3=$(echo "$MOST_USED_CLIENT" | jq -r '.[2].Client' | sed 's/Discord Jellyfin Music Bot/SiennaTunes/')
CLIENT_4=$(echo "$MOST_USED_CLIENT" | jq -r '.[3].Client' | sed 's/Discord Jellyfin Music Bot/SiennaTunes/')

#==============================================================================
# Data Processing - User Watch Times
#==============================================================================
# Extract and format watch times for each user
GARNET_WATCH_TIME=$(echo "$USER_ACTIVITY" | jq -r '.[] | select(.UserName=="Garnet") | .TotalWatchTime // 0')
GARNET_WATCH_TIME_FMT=$(format_watch_time "$GARNET_WATCH_TIME")
RASTA_WATCH_TIME=$(echo "$USER_ACTIVITY" | jq -r '.[] | select(.UserName=="Rasta") | .TotalWatchTime // 0')
RASTA_WATCH_TIME_FMT=$(format_watch_time "$RASTA_WATCH_TIME")
CHE_WATCH_TIME=$(echo "$USER_ACTIVITY" | jq -r '.[] | select(.UserName=="Che") | .TotalWatchTime // 0')
CHE_WATCH_TIME_FMT=$(format_watch_time "$CHE_WATCH_TIME")
FEATHER_WATCH_TIME=$(echo "$USER_ACTIVITY" | jq -r '.[] | select(.UserName=="Feather") | .TotalWatchTime // 0')
FEATHER_WATCH_TIME_FMT=$(format_watch_time "$FEATHER_WATCH_TIME")

#==============================================================================
# Data Processing - Immich User Stats
#==============================================================================
# Extract user-specific statistics
GARNET_STATS=$(echo "$IMMICH_DATA" | jq -r '.usageByUser[] | select(.userName=="Garnet") | {usage, photos}')
RASTA_STATS=$(echo "$IMMICH_DATA" | jq -r '.usageByUser[] | select(.userName=="Rasta") | {usage, photos}')
CHE_STATS=$(echo "$IMMICH_DATA" | jq -r '.usageByUser[] | select(.userName=="Che") | {usage, photos}')
FEATHER_STATS=$(echo "$IMMICH_DATA" | jq -r '.usageByUser[] | select(.userName=="Feather") | {usage, photos}')

# Extract individual storage and photo counts
GARNET_STORAGE=$(echo "$GARNET_STATS" | jq -r '.usage // 0')
GARNET_PHOTOS=$(echo "$GARNET_STATS" | jq -r '.photos // 0')

RASTA_STORAGE=$(echo "$RASTA_STATS" | jq -r '.usage // 0')
RASTA_PHOTOS=$(echo "$RASTA_STATS" | jq -r '.photos // 0')

CHE_STORAGE=$(echo "$CHE_STATS" | jq -r '.usage // 0')
CHE_PHOTOS=$(echo "$CHE_STATS" | jq -r '.photos // 0')

FEATHER_STORAGE=$(echo "$FEATHER_STATS" | jq -r '.usage // 0')
FEATHER_PHOTOS=$(echo "$FEATHER_STATS" | jq -r '.photos // 0')

# Calculate total statistics
TOTAL_STORAGE=$(echo "$IMMICH_DATA" | jq -r '.usage // 0')
TOTAL_PHOTOS=$(echo "$IMMICH_DATA" | jq -r '.photos // 0')

#==============================================================================
# JSON Output Generation
#==============================================================================
# Initialize JSON file with documents section
echo "{" > "$OUTPUT_FILE"
echo "    \"documents\": {" >> "$OUTPUT_FILE"

# Process document directories and calculate statistics
first=true
total_size=0
total_files=0

# Iterate through user document directories
for dir in "/path/to/documents/user" "/path/to/documents/user" "/path/to/documents/user" "/path/to/documents/user"; do
    name=$(basename "$dir" | tr '[:upper:]' '[:lower:]')
    current_mtime=$(stat -c %Y "$dir" 2>/dev/null)
    status="scanned"
    
    # Check and validate cache
    if [ -f "$CACHE_FILE" ] && cached_line=$(grep "^$dir:" "$CACHE_FILE" 2>/dev/null); then
        cached_mtime=$(echo "$cached_line" | cut -d: -f2)
        if [ "$current_mtime" = "$cached_mtime" ]; then
            dir_size=$(echo "$cached_line" | cut -d: -f3)
            file_count=$(echo "$cached_line" | cut -d: -f4)
            status="cached"
        fi
    fi
    
    # Recalculate if cache invalid
    if [ "$status" = "scanned" ]; then
        dir_size=$(du -sb "$dir" 2>/dev/null | cut -f1)
        file_count=$(find "$dir" -type f 2>/dev/null | wc -l)
        echo "$dir:$current_mtime:$dir_size:$file_count" >> "$CACHE_FILE.tmp"
    fi
    
    # Update totals
    total_size=$((total_size + dir_size))
    total_files=$((total_files + file_count))
    
    # Write user statistics to JSON
    if [ "$first" = true ]; then
        echo "        \"$name\": {" >> "$OUTPUT_FILE"
        first=false
    else
        echo "        ,\"$name\": {" >> "$OUTPUT_FILE"
    fi
    echo "            \"stats\": \"$(format_user_stats $dir_size $file_count "files")\"" >> "$OUTPUT_FILE"
    echo "        }" >> "$OUTPUT_FILE"
done

# Add combined document statistics
echo "        ,\"combined\": {" >> "$OUTPUT_FILE"
echo "            \"storage\": $total_size," >> "$OUTPUT_FILE"
echo "            \"files\": $total_files" >> "$OUTPUT_FILE"
echo "        }" >> "$OUTPUT_FILE"
echo "    }," >> "$OUTPUT_FILE"

#==============================================================================
# Complete JSON Output
#==============================================================================
# Append remaining sections (photos, music, jellyfin, clips) to JSON file
cat << EOF >> "$OUTPUT_FILE"
   "photos": {
       "garnet": {
           "stats": "$(format_user_stats "$GARNET_STORAGE" "$GARNET_PHOTOS" "photos")"
       },
       "rasta": {
           "stats": "$(format_user_stats "$RASTA_STORAGE" "$RASTA_PHOTOS" "photos")"
       },
       "che": {
           "stats": "$(format_user_stats "$CHE_STORAGE" "$CHE_PHOTOS" "photos")"
       },
       "feather": {
           "stats": "$(format_user_stats "$FEATHER_STORAGE" "$FEATHER_PHOTOS" "photos")"
       },
       "combined": {
           "storage": $TOTAL_STORAGE,
           "photos": $TOTAL_PHOTOS
       }
   },
   "music": {
       "garnet": {
           "stats": "$(format_user_stats $(echo "$GARNET_MUSIC" | jq -r '.Size // 0') $(echo "$GARNET_MUSIC" | jq -r '.files // 0') "songs")"
       },
       "rasta": {
           "stats": "$(format_user_stats $(echo "$RASTA_MUSIC" | jq -r '.Size // 0') $(echo "$RASTA_MUSIC" | jq -r '.files // 0') "songs")"
       },
       "che": {
           "stats": "$(format_user_stats $(echo "$CHE_MUSIC" | jq -r '.Size // 0') $(echo "$CHE_MUSIC" | jq -r '.files // 0') "songs")"
       },
       "graham": {
           "stats": "$(format_user_stats $(echo "$GRAHAM_MUSIC" | jq -r '.Size // 0') $(echo "$GRAHAM_MUSIC" | jq -r '.files // 0') "songs")"
       },
       "combined": {
           "size": $COMBINED_SIZE,
           "files": $COMBINED_FILES
       }
   },
   "jellyfin": {
       "movies": {
           "runtime": "$MOVIES_RUNTIME_FMT",
           "plays": $MOVIES_PLAYS,
           "storage": "$(format_size $MOVIES_STORAGE)",
           "count": $MOVIES_COUNT
       },
       "series": {
           "runtime": "$SHOWS_RUNTIME_FMT",
           "plays": $SHOWS_PLAYS,
           "storage": "$(format_size $SHOWS_STORAGE)",
           "count": $SHOWS_EPISODES
       },
       "popular_clients": {
           "first": "$CLIENT_1",
           "second": "$CLIENT_2",
           "third": "$CLIENT_3",
           "fourth": "$CLIENT_4"
       },
       "watch_time": {
           "garnet": "$GARNET_WATCH_TIME_FMT",
           "rasta": "$RASTA_WATCH_TIME_FMT",
           "che": "$CHE_WATCH_TIME_FMT",
           "feather": "$FEATHER_WATCH_TIME_FMT"
       }
   },
   "clips": {
       "size": $(get_clips_size "/path/to/media/clips"),
       "files": $(get_clips_files "/path/to/media/clips"),
       "game_count": $(get_game_count "/path/to/media/clips"),
       "runtime": "$(get_clips_runtime "/path/to/media/clips")"
   },
   "timestamp": $(date +%s000)
}
EOF

#==============================================================================
# Cleanup and Cache Management
#==============================================================================
# Update cache file atomically to prevent corruption
if [ -f "$CACHE_FILE.tmp" ]; then
   mv "$CACHE_FILE.tmp" "$CACHE_FILE"
fi