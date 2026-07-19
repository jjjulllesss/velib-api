from datetime import datetime, timezone
from typing import List, Optional, Union, Dict, Any
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.helpers import (
    fetch_all_stations,
    get_bike_sort_key,
    generate_summary,
)

app = FastAPI(
    title="Velib Commute API",
    description="API serverless pour optimiser les trajets Vélib (vélos mécaniques et bornes libres)",
    version="1.0.0",
)

# Enable CORS for convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Schemas for Response ---

class StartStationUsed(BaseModel):
    id: int = Field(..., description="ID de la station de départ sélectionnée")
    name: str = Field(..., description="Nom de la station")
    priority: int = Field(..., description="Priorité de la station dans la liste (0 = principale)")

class SelectedBike(BaseModel):
    id: str = Field(..., description="ID unique du vélo")
    dockPosition: str = Field(..., description="Position de la borne")
    score: int = Field(..., description="Score qualité du vélo")
    bikeRate: int = Field(..., description="Note globale du vélo")
    lastRideTime: Optional[str] = Field(None, description="Dernière heure d'utilisation ISO 8601 (peut être null)")
    station_name: Optional[str] = Field(None, description="Nom de la station du vélo")

class EndStationUsed(BaseModel):
    id: int = Field(..., description="ID de la station d'arrivée sélectionnée")
    name: str = Field(..., description="Nom de la station")
    priority: int = Field(..., description="Priorité de la station dans la liste (0 = principale)")
    docks_available: int = Field(..., description="Nombre de bornes disponibles pour stationner")

class CommuteResponse(BaseModel):
    ok: bool = Field(..., description="Statut de la requête")
    start_station_used: Optional[StartStationUsed] = Field(None, description="Station de départ retenue (None si aucun vélo)")
    selected_mechanical_bikes: List[SelectedBike] = Field(..., description="Liste des meilleurs vélos mécaniques (max 3)")
    selected_electric_bikes: List[SelectedBike] = Field(..., description="Liste des meilleurs vélos électriques (max 3)")
    start_fallback_used: bool = Field(..., description="Indique si une station de départ alternative a été utilisée")
    no_mechanical_available: bool = Field(..., description="Indique si aucun vélo mécanique n'était disponible")
    no_electric_available: bool = Field(..., description="Indique si aucun vélo électrique n'était disponible")
    end_station_used: EndStationUsed = Field(..., description="Station d'arrivée retenue (principale par défaut si aucune borne)")
    end_fallback_used: bool = Field(..., description="Indique si une station d'arrivée alternative a été utilisée")
    no_docks_available: bool = Field(..., description="Indique si aucune borne n'est disponible sur le trajet")
    summary: str = Field(..., description="Résumé lisible en anglais pour Apple Shortcuts")
    checked_at: str = Field(..., description="Date et heure de vérification au format ISO 8601 UTC")

# --- Query Params Parser ---

def parse_ids(param_name: str, val: Optional[str]) -> List[int]:
    """
    Parses and validates a comma-separated list of integers from query parameters.
    Raises a 400 Bad Request error if input is empty or invalid.
    """
    if not val or not val.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Le paramètre '{param_name}' est obligatoire et ne doit pas être vide."
        )
    
    parts = [p.strip() for p in val.split(",") if p.strip()]
    if not parts:
        raise HTTPException(
            status_code=400,
            detail=f"Le paramètre '{param_name}' doit contenir au moins un identifiant de station."
        )
        
    ids = []
    for p in parts:
        if not p.isdigit():
            raise HTTPException(
                status_code=400,
                detail=f"Identifiant de station invalide '{p}' dans '{param_name}'. Ce doit être un entier."
            )
        ids.append(int(p))
    return ids

# --- Helper logic for picking bikes of a specific type ---

def is_bike_valid(bike: Dict[str, Any], bike_type: str) -> bool:
    """
    Checks if a bike satisfies the criteria:
    - type matches
    - status is "available"
    - bikeRate is exactly 3 (bikes with bikeRate != 3 are not considered)
    - for electric bikes, battery_level must be >= 20 (battery_level < 20 is not considered)
    """
    if bike.get("type") != bike_type:
        return False
    if bike.get("status") != "available":
        return False
    if bike.get("bikeRate") != 3:
        return False
    if bike_type == "electric":
        bat = bike.get("battery_level")
        if bat is not None:
            try:
                if float(bat) < 20:
                    return False
            except (ValueError, TypeError):
                pass
    return True

def select_bikes_for_type(
    bike_type: str,
    start_ids: List[int],
    station_map: Dict[int, Dict[str, Any]]
) -> tuple[List[SelectedBike], List[Dict[str, Any]], bool, bool]:
    """
    Selects bikes of a specific type (mechanical or electric) following the rules:
    - Checks primary station. If it has <= 1 bike or no bike with score >= 80, it searches other stations as well.
    - Caps at 3 bikes.

    Returns:
    - selected_bikes: List of SelectedBike objects
    - stations_used_info: List of info dicts for stations from which bikes were taken
    - primary_had_zero: boolean indicating primary station had 0 bikes of this type
    - primary_had_insufficient: boolean indicating primary station had only 1 bike or no bike >= 80
    """
    selected_bikes = []
    stations_used_info = []
    primary_had_zero = False
    primary_had_insufficient = False

    # Check primary station's bikes of this type
    primary_sid = start_ids[0]
    primary_station = station_map.get(primary_sid)
    if primary_station:
        primary_typed_bikes = [
            b for b in primary_station.get("bikes", [])
            if is_bike_valid(b, bike_type)
        ]
        if not primary_typed_bikes:
            primary_had_zero = True
        else:
            has_score_80_or_more = any(int(b.get("score", 0)) >= 80 for b in primary_typed_bikes)
            if len(primary_typed_bikes) == 1 or not has_score_80_or_more:
                primary_had_insufficient = True

    for idx, sid in enumerate(start_ids):
        station = station_map.get(sid)
        if not station:
            continue

        bikes = station.get("bikes", [])
        typed_bikes = [
            b for b in bikes
            if is_bike_valid(b, bike_type)
        ]

        if typed_bikes:
            typed_bikes.sort(key=get_bike_sort_key)

            remaining_slots = 3 - len(selected_bikes)
            if remaining_slots <= 0:
                break

            top_bikes = typed_bikes[:remaining_slots]
            for b in top_bikes:
                selected_bikes.append(
                    SelectedBike(
                        id=str(b.get("id", "")),
                        dockPosition=str(b.get("dockPosition", "")),
                        score=int(b.get("score", 0)),
                        bikeRate=int(b.get("bikeRate", 0)),
                        lastRideTime=b.get("lastRideTime"),
                        station_name=station.get("name", f"Station {sid}")
                    )
                )

            stations_used_info.append({
                "id": sid,
                "name": station.get("name", f"Station {sid}"),
                "priority": idx,
                "bikes_count": len(top_bikes)
            })

            # Check if we should stop searching other stations
            has_at_least_two = len(selected_bikes) >= 2
            has_score_80 = any(b.score >= 80 for b in selected_bikes)
            if has_at_least_two and has_score_80:
                break

    return selected_bikes, stations_used_info, primary_had_zero, primary_had_insufficient

# --- Main API Endpoint ---

@app.get("/api/commute", response_model=CommuteResponse)
@app.get("/commute", response_model=CommuteResponse)
async def get_commute(
    start: Optional[str] = Query(None, description="Liste d'IDs de stations de départ (ex: 42024,1003)"),
    end: Optional[str] = Query(None, description="Liste d'IDs de stations d'arrivée (ex: 13053,13052)")
):
    # Parse and validate inputs (deduplicate and limit to 5 maximum stations to prevent upstream rate-limiting)
    start_ids = list(dict.fromkeys(parse_ids("start", start)))[:5]
    end_ids = list(dict.fromkeys(parse_ids("end", end)))[:5]

    # 1. Fetch details for all stations concurrently
    station_map = await fetch_all_stations(start_ids + end_ids)

    # 2. Check if all requests failed upstream
    requested_ids = set(start_ids + end_ids)
    if not any(sid in station_map for sid in requested_ids):
        raise HTTPException(
            status_code=502,
            detail="Toutes les requêtes de stations auprès de l'API Velib ont échoué."
        )

    # 3. Start selection logic (mechanical & electric bikes)
    selected_mechanical_bikes, mech_stations_info, mech_primary_zero, mech_primary_insufficient = select_bikes_for_type(
        "mechanical", start_ids, station_map
    )
    selected_electric_bikes, elec_stations_info, elec_primary_zero, elec_primary_insufficient = select_bikes_for_type(
        "electric", start_ids, station_map
    )

    no_mechanical_available = len(selected_mechanical_bikes) == 0
    no_electric_available = len(selected_electric_bikes) == 0

    # Determine start_station_used and fallback
    start_station_used = None
    start_fallback_used = False

    # Collect all stations from which we got either mechanical or electric bikes
    combined_stations_info = []
    seen_ids = set()
    for s_info in (mech_stations_info + elec_stations_info):
        if s_info["id"] not in seen_ids:
            seen_ids.add(s_info["id"])
            combined_stations_info.append(s_info)

    # Sort them by priority
    combined_stations_info.sort(key=lambda x: x["priority"])

    if combined_stations_info:
        first_used = combined_stations_info[0]
        start_station_used = StartStationUsed(
            id=first_used["id"],
            name=first_used["name"],
            priority=first_used["priority"]
        )
        start_fallback_used = any(info["priority"] > 0 for info in combined_stations_info)

    # 4. End selection logic (available docks)
    end_station_used = None
    end_fallback_used = False
    no_docks_available = True

    for idx, eid in enumerate(end_ids):
        station = station_map.get(eid)
        if not station:
            continue

        docks = station.get("docks_available", 0)
        if docks > 0:
            end_station_used = EndStationUsed(
                id=eid,
                name=station.get("name", f"Station {eid}"),
                priority=idx,
                docks_available=docks
            )
            end_fallback_used = idx > 0
            no_docks_available = False
            break

    # If no end station has free docks, fallback to primary end station
    if no_docks_available:
        primary_eid = end_ids[0]
        station = station_map.get(primary_eid)
        if station:
            name = station.get("name", f"Station {primary_eid}")
            docks = station.get("docks_available", 0)
        else:
            name = f"Station {primary_eid}"
            docks = 0

        end_station_used = EndStationUsed(
            id=primary_eid,
            name=name,
            priority=0,
            docks_available=docks
        )
        end_fallback_used = False

    # 5. Generate summary
    summary_text = generate_summary(
        no_mechanical_available=no_mechanical_available,
        no_electric_available=no_electric_available,
        start_fallback_used=start_fallback_used,
        start_station_used=start_station_used.model_dump() if start_station_used else None,
        selected_mechanical_bikes=[b.model_dump() for b in selected_mechanical_bikes],
        selected_electric_bikes=[b.model_dump() for b in selected_electric_bikes],
        no_docks_available=no_docks_available,
        end_fallback_used=end_fallback_used,
        end_station_used=end_station_used.model_dump(),
        mech_primary_zero=mech_primary_zero,
        mech_primary_insufficient=mech_primary_insufficient,
        mech_stations_info=mech_stations_info,
        elec_primary_zero=elec_primary_zero,
        elec_primary_insufficient=elec_primary_insufficient,
        elec_stations_info=elec_stations_info
    )

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return CommuteResponse(
        ok=True,
        start_station_used=start_station_used,
        selected_mechanical_bikes=selected_mechanical_bikes,
        selected_electric_bikes=selected_electric_bikes,
        start_fallback_used=start_fallback_used,
        no_mechanical_available=no_mechanical_available,
        no_electric_available=no_electric_available,
        end_station_used=end_station_used,
        end_fallback_used=end_fallback_used,
        no_docks_available=no_docks_available,
        summary=summary_text,
        checked_at=checked_at
    )
