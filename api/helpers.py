import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import httpx

# Upstream API URL template
STATION_API_TEMPLATE = "https://tdqr.ovh/api/stations/station_{station_id}/details"

def parse_ride_time(val: Any) -> datetime:
    """
    Safely parses a ISO 8601 ride time string into a datetime object.
    Returns datetime.min with UTC timezone if parsing fails or if value is empty/invalid.
    """
    if not val or not isinstance(val, str):
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        # standard ISO format parsing, handling 'Z' suffix
        cleaned = val.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)

def get_bike_sort_key(bike: Dict[str, Any]) -> Tuple[float, float, float, str]:
    """
    Computes a sort key for ranking bikes:
    1. score descending (negated)
    2. bikeRate descending (negated)
    3. lastRideTime descending (negated timestamp)
    4. id ascending (lexicographical)
    """
    score = bike.get("score")
    try:
        score_val = float(score) if score is not None else 0.0
    except (ValueError, TypeError):
        score_val = 0.0

    rate = bike.get("bikeRate")
    try:
        rate_val = float(rate) if rate is not None else 0.0
    except (ValueError, TypeError):
        rate_val = 0.0

    dt = parse_ride_time(bike.get("lastRideTime"))
    ts_val = dt.timestamp()

    bike_id = str(bike.get("id", ""))

    # We sort ascending using this key
    return (-score_val, -rate_val, -ts_val, bike_id)

async def fetch_station_details(client: httpx.AsyncClient, station_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetches the details for a single station from the upstream API.
    Returns the 'data' dictionary on success, or None on failure/invalid responses.
    """
    url = STATION_API_TEMPLATE.format(station_id=station_id)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        response = await client.get(url, headers=headers, timeout=3.0)
        if response.status_code == 200:
            json_data = response.json()
            if json_data.get("success") is True and "data" in json_data:
                return json_data["data"]
    except Exception:
        # Ignore exceptions and proceed to let higher layers handle failures
        pass
    return None

async def fetch_all_stations(station_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """
    Fetches details for all provided station IDs concurrently.
    Returns a dictionary mapping station_id to its data.
    """
    unique_ids = list(dict.fromkeys(station_ids))
    if not unique_ids:
        return {}

    async with httpx.AsyncClient() as client:
        tasks = [fetch_station_details(client, sid) for sid in unique_ids]
        results = await asyncio.gather(*tasks)

    return {sid: res for sid, res in zip(unique_ids, results) if res is not None}

def generate_summary(
    no_mechanical_available: bool,
    no_electric_available: bool,
    start_fallback_used: bool,
    start_station_used: Optional[Dict[str, Any]],
    selected_mechanical_bikes: List[Dict[str, Any]],
    selected_electric_bikes: List[Dict[str, Any]],
    no_docks_available: bool,
    end_fallback_used: bool,
    end_station_used: Dict[str, Any],
    mech_primary_zero: bool = False,
    mech_primary_insufficient: bool = False,
    mech_stations_info: List[Dict[str, Any]] = None,
    elec_primary_zero: bool = False,
    elec_primary_insufficient: bool = False,
    elec_stations_info: List[Dict[str, Any]] = None
) -> str:
    """
    Generates a human-readable English summary for Apple Shortcuts.
    """
    if no_mechanical_available and no_electric_available:
        start_summary = "No bikes available on the departure stations."
    else:
        # We found some bikes. Let's describe the departure situation.
        station_name = start_station_used["name"] if start_station_used else "Unknown"
        
        mech_parts = []
        elec_parts = []

        num_mech = len(selected_mechanical_bikes)
        num_elec = len(selected_electric_bikes)

        # 1. Determine mechanical message
        if num_mech > 0:
            mech_word = "mechanical bike" if num_mech == 1 else "mechanical bikes"
            if mech_primary_insufficient and len(mech_stations_info) > 1:
                other_names = [info["name"] for info in mech_stations_info if info["priority"] > 0]
                others_str = ", ".join(other_names)
                mech_parts.append(f"Not enough mechanical bikes on primary station, fallback to {others_str} ({num_mech} found)")
            elif mech_primary_zero and start_fallback_used:
                mech_parts.append(f"No mechanical bikes on primary station, fallback to {station_name} ({num_mech} found)")
            else:
                mech_parts.append(f"{num_mech} {mech_word} found")
        else:
            mech_parts.append("no mechanical bikes found")

        # 2. Determine electric message
        if num_elec > 0:
            elec_word = "electric bike" if num_elec == 1 else "electric bikes"
            if elec_primary_insufficient and len(elec_stations_info) > 1:
                other_names = [info["name"] for info in elec_stations_info if info["priority"] > 0]
                others_str = ", ".join(other_names)
                elec_parts.append(f"Not enough electric bikes on primary station, fallback to {others_str} ({num_elec} found)")
            elif elec_primary_zero and start_fallback_used:
                elec_parts.append(f"No electric bikes on primary station, fallback to {station_name} ({num_elec} found)")
            else:
                elec_parts.append(f"{num_elec} {elec_word} found")
        else:
            elec_parts.append("no electric bikes found")

        start_summary = f"Departure {station_name}: {', '.join(mech_parts)} and {', '.join(elec_parts)}."

    # Arrival Part
    if no_docks_available:
        end_summary = "No free docks on arrival stations."
    else:
        num_docks = end_station_used.get("docks_available", 0)
        dock_word = "dock" if num_docks == 1 else "docks"
        avail_word = "available"
        
        station_name = end_station_used["name"]
        if end_fallback_used:
            end_summary = f"No free docks on primary arrival station, fallback to {station_name}, {num_docks} {dock_word} {avail_word}."
        else:
            end_summary = f"Arrival {station_name}, {num_docks} {dock_word} {avail_word}."

    return f"{start_summary} {end_summary}"
