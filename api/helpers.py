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
    Generates a structured English summary for Apple Shortcuts:

    Mechanical:
    - Position (score) station
    ...

    Electrical:
    - Position (score) station
    ...

    Arrival: docks - station
    """
    # Mechanical section
    mech_lines = ["Mechanical:"]
    if selected_mechanical_bikes:
        for bike in selected_mechanical_bikes:
            pos = bike.get("dockPosition", "")
            score = bike.get("score", 0)
            station = bike.get("station_name", "Unknown")
            mech_lines.append(f"- {pos} ({score}) {station}")
    else:
        mech_lines.append("- None")
        
    # Electrical section
    elec_lines = ["Electrical:"]
    if selected_electric_bikes:
        for bike in selected_electric_bikes:
            pos = bike.get("dockPosition", "")
            score = bike.get("score", 0)
            station = bike.get("station_name", "Unknown")
            elec_lines.append(f"- {pos} ({score}) {station}")
    else:
        elec_lines.append("- None")
        
    # Arrival section
    docks = end_station_used.get("docks_available", 0)
    station = end_station_used.get("name", "Unknown")
    arrival_line = f"Arrival: {docks} - {station}"

    return "\n\n".join([
        "\n".join(mech_lines),
        "\n".join(elec_lines),
        arrival_line
    ])
