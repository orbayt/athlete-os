import os
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://intervals.icu/api/v1"
API_KEY = os.getenv("INTERVALS_API_KEY")


def get_activities(oldest: date, newest: date):
    if not API_KEY:
        raise RuntimeError("INTERVALS_API_KEY is not set")

    response = httpx.get(
        f"{BASE_URL}/athlete/0/activities",
        params={
            "oldest": oldest.isoformat(),
            "newest": newest.isoformat(),
        },
        auth=("API_KEY", API_KEY),
        timeout=30.0,
    )

    response.raise_for_status()
    return response.json()


def get_recent_activities(days: int = 30):
    newest = date.today()
    oldest = newest - timedelta(days=days -1)

    return get_activities(oldest, newest)


def normalize_activity(activity: dict) -> dict:
    distance_m = activity.get("icu_distance") or activity.get("distance")
    moving_time_s = activity.get("moving_time")

    return {
        "id": activity.get("id"),
        "date": activity.get("start_date_local"),
        "name": activity.get("name"),
        "type": activity.get("type"),
        "distance_km": (
            round(distance_m / 1000, 2)
            if distance_m is not None
            else None
        ),
        "moving_time_min": (
            round(moving_time_s / 60, 1)
            if moving_time_s is not None
            else None
        ),
        "elevation_gain_m": activity.get("total_elevation_gain"),
        "training_load": activity.get("icu_training_load"),
        "average_hr": activity.get("average_heartrate"),
        "max_hr": activity.get("max_heartrate"),
    }


def get_activities_normalized(
    oldest: date,
    newest: date,
) -> list[dict]:
    activities = get_activities(oldest, newest)

    return [
        normalize_activity(activity)
        for activity in activities
    ]


def get_recent_activities_normalized(days: int = 30) -> list[dict]:
    newest = date.today()
    oldest = newest - timedelta(days=days - 1)

    return get_activities_normalized(oldest, newest)

def get_wellness(oldest: date, newest: date):
    if not API_KEY:
        raise RuntimeError("INTERVALS_API_KEY is not set")

    response = httpx.get(
        f"{BASE_URL}/athlete/0/wellness",
        params={
            "oldest": oldest.isoformat(),
            "newest": newest.isoformat(),
        },
        auth=("API_KEY", API_KEY),
        timeout=30.0,
    )

    response.raise_for_status()
    return response.json()


def get_recent_wellness(days: int = 7):
    newest = date.today()
    oldest = newest - timedelta(days=days - 1)

    return get_wellness(oldest, newest)

def normalize_wellness(wellness: dict) -> dict:
    sleep_secs = wellness.get("sleepSecs")

    weight = wellness.get("weight")
    if wellness.get("tempWeight"):
        weight = None

    return {
        "date": wellness.get("id"),
        "resting_hr": wellness.get("restingHR"),
        "hrv_rmssd": wellness.get("hrv"),
        "sleep_hours": (
            round(sleep_secs / 3600, 2)
            if sleep_secs is not None
            else None
        ),
        "sleep_score": wellness.get("sleepScore"),
        "steps": wellness.get("steps"),
        "weight_kg": weight,
        "readiness": wellness.get("readiness"),
        "fitness_ctl": wellness.get("ctl"),
        "fatigue_atl": wellness.get("atl"),
        "ramp_rate": wellness.get("rampRate"),
        "soreness": wellness.get("soreness"),
        "stress": wellness.get("stress"),
        "mood": wellness.get("mood"),
        "motivation": wellness.get("motivation"),
        "spo2": wellness.get("spO2"),
    }


def get_wellness_normalized(
    oldest: date,
    newest: date,
) -> list[dict]:
    records = get_wellness(oldest, newest)

    return [
        normalize_wellness(record)
        for record in records
    ]


def get_recent_wellness_normalized(days: int = 7) -> list[dict]:
    newest = date.today()
    oldest = newest - timedelta(days=days - 1)

    return get_wellness_normalized(oldest, newest)