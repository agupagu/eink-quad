"""Independent keyless worldwide flight lookup; never writes Sky's caches.

``normalize_flight`` raises ValueError for invalid input (empty is allowed).
``track_flight`` returns live/not_found/error, never a scheduled-flight claim.
checked_at is Unix seconds of the last network attempt; sighting.seen_at is
position time. An error may carry a last-good sighting, which is NOT live.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, replace
import math
import re
import threading
import time
import unicodedata

import requests
from urllib3.util import Timeout

import adsb
from render import Sighting

_IATA = re.compile(r"[A-Z0-9]{2}[0-9]{1,4}[A-Z]?")
_ICAO = re.compile(r"[A-Z]{3}(?=[A-Z0-9]*[0-9])[A-Z0-9]{1,4}")
CACHE_TTL = 60
CACHE_MAX = 32
MAX_POSITION_AGE = 120
_TIMEOUT = Timeout(total=7, connect=2, read=5)  # two lookups, no retries/redirects
_cache = OrderedDict()
_lock = threading.Lock()


def normalize_flight(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Flight number must be text")
    # Validate before case conversion/whitespace removal: never repair symbols,
    # Unicode lookalikes or control characters into a valid identifier.
    if any(unicodedata.category(c).startswith("C") or
           c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
           for c in value):
        raise ValueError("Use an IATA flight number or ICAO callsign, without punctuation")
    value = "".join(value.split()).upper()
    if len(value) > 10 or (value and not (_IATA.fullmatch(value) or _ICAO.fullmatch(value))):
        raise ValueError("Invalid flight number or callsign")
    return value


@dataclass
class FlightTrack:
    query: str
    callsign: str = ""
    sighting: Sighting | None = None
    error: str | None = None
    checked_at: float = 0.0
    status: str = "not_found"
    origin: str = ""
    dest: str = ""
    airline: str = ""


def _number(value, low=-math.inf, high=math.inf):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _get(url):
    # requests forwards urllib3's total/connect/read budget unchanged.
    response = requests.get(url, headers={"User-Agent": adsb.UA},
                            timeout=_TIMEOUT,  # type: ignore[arg-type]
                            allow_redirects=False)
    if response.status_code != 200:
        response.raise_for_status()
        raise ValueError("Unexpected flight API HTTP status")
    response.raise_for_status()
    return response.json()


def _route(query):
    try:
        data = _get(f"https://api.adsbdb.com/v0/callsign/{query}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    if not isinstance(data, dict) or not isinstance(data.get("response"), dict):
        raise ValueError("Invalid route response")
    route = data["response"].get("flightroute")
    if not isinstance(route, dict):
        raise ValueError("Invalid route response")
    # The exact queried identifier must be represented in the authoritative
    # response. A similarly named route is not permission to query another jet.
    identifiers = [_text(route.get(k)).upper() for k in ("callsign", "callsign_iata", "callsign_icao")]
    canonical = _text(route.get("callsign_icao")).upper()
    if (query not in identifiers or not _ICAO.fullmatch(canonical)
            or (_ICAO.fullmatch(query) and canonical != query)):
        raise ValueError("Route callsign mismatch")
    return route


def _route_info(route):
    out = {}
    for source, prefix in (("origin", "origin"), ("destination", "dest")):
        airport = route.get(source) or {}
        if not isinstance(airport, dict):
            raise ValueError("Invalid route airport")
        out[prefix + "_iata"] = _text(airport.get("iata_code"))
        out[prefix + "_city"] = _text(airport.get("municipality"))
        lat, lon = airport.get("latitude"), airport.get("longitude")
        out[prefix + "_lat"] = lat if _number(lat, -90, 90) else None
        out[prefix + "_lon"] = lon if _number(lon, -180, 180) else None
    airline = route.get("airline") or {}
    if not isinstance(airline, dict):
        raise ValueError("Invalid route airline")
    out["airline"] = _text(airline.get("name"))
    return out


def _live(callsign, now):
    data = _get(f"https://api.adsb.lol/v2/callsign/{callsign}")
    if (not isinstance(data, dict) or data.get("msg") != "No error"
            or not isinstance(data.get("ac"), list)
            or any(not isinstance(ac, dict) for ac in data.get("ac", []))
            or not _number(data.get("now"), 1)
            or not _number(data.get("total"), 0)
            or data["total"] != len(data["ac"])):
        raise ValueError("Invalid live aircraft response")
    feed_time = data["now"] / 1000
    if feed_time > now + 30 or now - feed_time > MAX_POSITION_AGE:
        raise ValueError("Stale live aircraft feed")
    matches = [ac for ac in data["ac"] if isinstance(ac, dict)
               and _text(ac.get("flight")).upper() == callsign]
    if len(matches) > 1:
        raise ValueError("Ambiguous callsign: multiple aircraft")
    if not matches:
        return None
    raw = matches[0]
    age = raw.get("seen_pos")
    if (not _number(age, 0, MAX_POSITION_AGE)
            or now - feed_time + age > MAX_POSITION_AGE
            or not _number(raw.get("lat"), -90, 90)
            or not _number(raw.get("lon"), -180, 180)):
        return None
    return adsb.Aircraft(raw), min(now, feed_time) - age


def _sighting(ac, seen_at, query, callsign, route, lat, lon):
    info = _route_info(route) if route else {}
    if info and not adsb.route_plausible(ac, info):
        info = {}
    altitude = ac.altitude
    altitude = altitude if _number(altitude) else None
    speed = ac.get("gs")
    speed = speed if _number(speed, 0) else None
    dist = adsb.haversine_nm(lat, lon, ac["lat"], ac["lon"])
    sighting = Sighting(
        callsign=callsign, airline=info.get("airline", ""),
        origin=info.get("origin_iata", ""), dest=info.get("dest_iata", ""),
        origin_city=info.get("origin_city", ""), dest_city=info.get("dest_city", ""),
        alt_ft=int(altitude) if altitude is not None else None, speed_kt=speed, dist_nm=dist,
        elev_deg=adsb.elevation_deg(dist, altitude or 0), seen_at=seen_at,
        flight_no=_text(route.get("callsign_iata")) if info else (query if not _ICAO.fullmatch(query) else ""),
        aircraft=_text(ac.get("t")), bearing=adsb.bearing_deg(lat, lon, ac["lat"], ac["lon"]))
    if info and all(info.get(k) is not None for k in ("origin_lat", "origin_lon", "dest_lat", "dest_lon")):
        remaining = adsb.haversine_nm(ac["lat"], ac["lon"], info["dest_lat"], info["dest_lon"])
        leg = adsb.haversine_nm(info["origin_lat"], info["origin_lon"], info["dest_lat"], info["dest_lon"])
        if leg > 0:
            sighting.progress = max(0.0, min(1.0, 1 - remaining / leg))
        # Straight-line estimate at current groundspeed, NOT a schedule.
        if speed is not None and speed > 50 and not ac.on_ground:
            sighting.eta_min = round(remaining / speed * 60)
        sighting.dest_dist_nm = adsb.haversine_nm(lat, lon, info["dest_lat"], info["dest_lon"])
    return sighting


def track_flight(query: str, lat: float, lon: float) -> FlightTrack:
    query = normalize_flight(query)
    if not _number(lat, -90, 90) or not _number(lon, -180, 180):
        raise ValueError("Invalid observer coordinates")
    if not query:
        return FlightTrack(query="", checked_at=time.time())
    key = (query, lat, lon)
    # A single bounded lock also prevents different requests from stampeding
    # volunteer APIs. No shared route cache, radar state, or disk is touched.
    if not _lock.acquire(timeout=2):
        return FlightTrack(query=query, checked_at=time.time(), status="error",
                           error="Flight lookup busy; try again shortly")
    try:
        previous = _cache.get(key)
        if previous and time.monotonic() - previous[0] < CACHE_TTL:
            _cache.move_to_end(key)
            result = deepcopy(previous[1])
            if result.status == "live" and time.time() - result.sighting.seen_at > MAX_POSITION_AGE:
                return replace(result, status="not_found", sighting=None)
            return result
        now = time.time()
        callsign = query if _ICAO.fullmatch(query) else ""
        result = FlightTrack(query=query, callsign=callsign, checked_at=now)
        try:
            try:
                route = _route(query)
            except (requests.RequestException, ValueError, TypeError):
                if not callsign:
                    raise
                route = None  # ICAO can always be queried directly.
            if not callsign and route:
                callsign = _text(route["callsign_icao"]).upper()
                result.callsign = callsign
            if callsign:
                found = _live(callsign, time.time())
                if found:
                    ac, seen_at = found
                    result.sighting = _sighting(ac, seen_at, query, callsign, route, lat, lon)
                    result.status = "live"
                    result.origin = result.sighting.origin
                    result.dest = result.sighting.dest
                    result.airline = result.sighting.airline
        except (requests.RequestException, ValueError, TypeError, KeyError, OverflowError) as exc:
            result.status = "error"
            result.error = "Flight lookup failed: " + str(exc)[:180]
            if previous and previous[1].sighting is not None:
                old = previous[1]
                result.sighting = deepcopy(old.sighting)
                result.callsign = old.callsign
                result.origin, result.dest, result.airline = old.origin, old.dest, old.airline
        _cache[key] = (time.monotonic(), deepcopy(result))
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
        return result
    finally:
        _lock.release()
