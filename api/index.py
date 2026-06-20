from datetime import datetime, timezone
from typing import List, Optional, Union
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

class EndStationUsed(BaseModel):
    id: int = Field(..., description="ID de la station d'arrivée sélectionnée")
    name: str = Field(..., description="Nom de la station")
    priority: int = Field(..., description="Priorité de la station dans la liste (0 = principale)")
    docks_available: int = Field(..., description="Nombre de bornes disponibles pour stationner")

class CommuteResponse(BaseModel):
    ok: bool = Field(..., description="Statut de la requête")
    start_station_used: Optional[StartStationUsed] = Field(None, description="Station de départ retenue (None si aucun vélo)")
    selected_bikes: List[SelectedBike] = Field(..., description="Liste des meilleurs vélos mécaniques (max 3)")
    start_fallback_used: bool = Field(..., description="Indique si une station de départ alternative a été utilisée")
    no_mechanical_available: bool = Field(..., description="Indique si aucun vélo mécanique n'était disponible")
    end_station_used: EndStationUsed = Field(..., description="Station d'arrivée retenue (principale par défaut si aucune borne)")
    end_fallback_used: bool = Field(..., description="Indique si une station d'arrivée alternative a été utilisée")
    no_docks_available: bool = Field(..., description="Indique si aucune borne n'est disponible sur le trajet")
    summary: str = Field(..., description="Résumé lisible en français pour Apple Shortcuts")
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

    # 3. Start selection logic (mechanical bikes)
    start_station_used = None
    selected_bikes = []
    start_fallback_used = False
    no_mechanical_available = True

    for idx, sid in enumerate(start_ids):
        station = station_map.get(sid)
        if not station:
            continue

        bikes = station.get("bikes", [])
        # filter mechanical & available
        mech_bikes = [
            b for b in bikes
            if b.get("type") == "mechanical" and b.get("status") == "available"
        ]

        if mech_bikes:
            # Sort bikes (score desc, bikeRate desc, lastRideTime desc, id asc)
            mech_bikes.sort(key=get_bike_sort_key)
            
            # Keep top 3
            top_bikes = mech_bikes[:3]
            selected_bikes = [
                SelectedBike(
                    id=str(b.get("id", "")),
                    dockPosition=str(b.get("dockPosition", "")),
                    score=int(b.get("score", 0)),
                    bikeRate=int(b.get("bikeRate", 0)),
                    lastRideTime=b.get("lastRideTime")
                )
                for b in top_bikes
            ]
            
            start_station_used = StartStationUsed(
                id=sid,
                name=station.get("name", f"Station {sid}"),
                priority=idx
            )
            start_fallback_used = idx > 0
            no_mechanical_available = False
            break

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
        start_fallback_used=start_fallback_used,
        start_station_used=start_station_used.model_dump() if start_station_used else None,
        selected_bikes=[b.model_dump() for b in selected_bikes],
        no_docks_available=no_docks_available,
        end_fallback_used=end_fallback_used,
        end_station_used=end_station_used.model_dump()
    )

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return CommuteResponse(
        ok=True,
        start_station_used=start_station_used,
        selected_bikes=selected_bikes,
        start_fallback_used=start_fallback_used,
        no_mechanical_available=no_mechanical_available,
        end_station_used=end_station_used,
        end_fallback_used=end_fallback_used,
        no_docks_available=no_docks_available,
        summary=summary_text,
        checked_at=checked_at
    )
