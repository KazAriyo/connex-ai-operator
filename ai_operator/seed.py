from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .clock import week_start

def build_seed(today: date) -> dict:
    ws = week_start(today)

    def this_week(days_back: int) -> date:
        return max(ws, today - timedelta(days=days_back))

    def d(offset: int) -> str:
        return (today + timedelta(days=offset)).isoformat()

    def dt(offset: int, hh: int, mm: int = 0) -> str:
        return datetime.combine(today + timedelta(days=offset), time(hh, mm)).isoformat(timespec="minutes")

    leads = [
        {
            "id": "L-001", "name": "Aisha Khan", "type": "buyer", "source": "Rightmove",
            "email": "aisha.khan@example.com", "phone": "07700 900101",
            "interest": "2-bed flat in Hackney or Dalston", "budget_gbp": 475000, "area": "Hackney",
            "status": "new", "assigned_to": "Sophie Turner",
            "created_on": this_week(0).isoformat(), "last_contacted": None,
            "notes": "Called at 21:04, first-time buyer, mortgage in principle agreed.",
        },
        {
            "id": "L-002", "name": "Tom Fletcher", "type": "tenant", "source": "Zoopla",
            "email": "tom.fletcher@example.com", "phone": "07700 900102",
            "interest": "1-bed rental near Stratford station", "budget_gbp": 1700, "area": "Stratford",
            "status": "new", "assigned_to": None,
            "created_on": this_week(1).isoformat(), "last_contacted": None,
            "notes": "Monthly budget. Wants to move within 6 weeks.",
        },
        {
            "id": "L-003", "name": "Grace Adeyemi", "type": "vendor", "source": "WhatsApp",
            "email": "grace.adeyemi@example.com", "phone": "07700 900103",
            "interest": "Valuation for 3-bed terrace in Walthamstow", "budget_gbp": None, "area": "Walthamstow",
            "status": "new", "assigned_to": None,
            "created_on": this_week(2).isoformat(), "last_contacted": None,
            "notes": "Thinking of selling in spring. Asked about fees.",
        },
        {
            "id": "L-004", "name": "Marcus Bell", "type": "buyer", "source": "Website",
            "email": "marcus.bell@example.com", "phone": "07700 900104",
            "interest": "3-bed house with garden, Leyton or Leytonstone", "budget_gbp": 650000, "area": "Leyton",
            "status": "contacted", "assigned_to": "Daniel Okafor",
            "created_on": d(-12), "last_contacted": d(-9),
            "notes": "Chain-free. Prefers Saturday viewings.",
        },
        {
            "id": "L-005", "name": "Hannah Liu", "type": "tenant", "source": "Rightmove",
            "email": "hannah.liu@example.com", "phone": "07700 900105",
            "interest": "2-bed rental in Bow, pet-friendly", "budget_gbp": 2100, "area": "Bow",
            "status": "viewing_booked", "assigned_to": "Priya Shah",
            "created_on": d(-10), "last_contacted": d(-2),
            "notes": "Has a small dog. Viewing booked for tomorrow.",
        },
        {
            "id": "L-006", "name": "Oliver Grant", "type": "landlord", "source": "Referral",
            "email": "oliver.grant@example.com", "phone": "07700 900106",
            "interest": "Fully managed letting for 2 flats in Canning Town", "budget_gbp": None, "area": "Canning Town",
            "status": "contacted", "assigned_to": "Sophie Turner",
            "created_on": d(-20), "last_contacted": d(-15),
            "notes": "Gas safety certificate due next month on one flat.",
        },
        {
            "id": "L-007", "name": "Fatima Rahman", "type": "buyer", "source": "Zoopla",
            "email": "fatima.rahman@example.com", "phone": "07700 900107",
            "interest": "1-bed flat in Bethnal Green", "budget_gbp": 420000, "area": "Bethnal Green",
            "status": "offer_made", "assigned_to": "Daniel Okafor",
            "created_on": d(-30), "last_contacted": d(-1),
            "notes": "Offer of £410,000 on 14 Roman Road flat, awaiting vendor.",
        },
        {
            "id": "L-008", "name": "Jack Morrison", "type": "buyer", "source": "Phone",
            "email": "jack.morrison@example.com", "phone": "07700 900108",
            "interest": "Studio or 1-bed, Mile End", "budget_gbp": 350000, "area": "Mile End",
            "status": "new", "assigned_to": None,
            "created_on": d(-11), "last_contacted": None,
            "notes": "Left a voicemail out of hours. Never called back.",
        },
        {
            "id": "L-009", "name": "Chloe Evans", "type": "vendor", "source": "Website",
            "email": "chloe.evans@example.com", "phone": "07700 900109",
            "interest": "Sell 2-bed maisonette in Forest Gate", "budget_gbp": None, "area": "Forest Gate",
            "status": "won", "assigned_to": "Priya Shah",
            "created_on": d(-45), "last_contacted": d(-5),
            "notes": "Instructed us. Listed on portals.",
        },
        {
            "id": "L-010", "name": "Ravi Patel", "type": "tenant", "source": "WhatsApp",
            "email": "ravi.patel@example.com", "phone": "07700 900110",
            "interest": "3-bed family rental in Ilford", "budget_gbp": 2400, "area": "Ilford",
            "status": "lost", "assigned_to": "Sophie Turner",
            "created_on": d(-25), "last_contacted": d(-18),
            "notes": "Found a property through another agent.",
        },
    ]

    tasks = [
        {"id": "T-001", "title": "Send Marcus Bell three garden listings", "due": d(1),
         "status": "open", "related_to": "L-004", "created_on": d(-3)},
        {"id": "T-002", "title": "Chase vendor on Fatima Rahman's offer", "due": d(0),
         "status": "open", "related_to": "L-007", "created_on": d(-1)},
        {"id": "T-003", "title": "Book gas safety check for Oliver Grant's flat", "due": d(-2),
         "status": "open", "related_to": "L-006", "created_on": d(-10)},
        {"id": "T-004", "title": "Upload Forest Gate photos to portals", "due": d(-4),
         "status": "done", "related_to": "L-009", "created_on": d(-8)},
        {"id": "T-005", "title": "Weekly lettings report", "due": d(3),
         "status": "open", "related_to": None, "created_on": d(-2)},
    ]

    events = [
        {"id": "E-001", "title": "Viewing: 2-bed flat, Bow (Hannah Liu)", "type": "viewing",
         "start": dt(1, 10, 0), "end": dt(1, 10, 30), "location": "Flat 4, 22 Fairfield Road, Bow",
         "lead_id": "L-005", "staff": "Priya Shah"},
        {"id": "E-002", "title": "Valuation: Forest Gate maisonette follow-up", "type": "valuation",
         "start": dt(1, 14, 0), "end": dt(1, 15, 0), "location": "Forest Gate",
         "lead_id": "L-009", "staff": "Priya Shah"},
        {"id": "E-003", "title": "Team meeting", "type": "meeting",
         "start": dt(0, 12, 30), "end": dt(0, 13, 30), "location": "Office",
         "lead_id": None, "staff": "Whole team"},
        {"id": "E-004", "title": "Call Fatima Rahman with vendor response", "type": "call",
         "start": dt(0, 16, 0), "end": dt(0, 16, 15), "location": "Phone",
         "lead_id": "L-007", "staff": "Daniel Okafor"},
        {"id": "E-005", "title": "Viewing: 3-bed house, Leytonstone (Marcus Bell)", "type": "viewing",
         "start": dt(4, 11, 0), "end": dt(4, 11, 45), "location": "18 Cann Hall Road, Leytonstone",
         "lead_id": "L-004", "staff": "Daniel Okafor"},
        {"id": "E-006", "title": "Landlord meeting: Oliver Grant", "type": "meeting",
         "start": dt(6, 15, 0), "end": dt(6, 16, 0), "location": "Office",
         "lead_id": "L-006", "staff": "Sophie Turner"},
        {"id": "E-007", "title": "Inspection: Canning Town flat 2", "type": "inspection",
         "start": dt(9, 9, 30), "end": dt(9, 10, 30), "location": "Canning Town",
         "lead_id": "L-006", "staff": "Sophie Turner"},
    ]

    return {
        "agency": "Eastside Homes (fictional demo agency)",
        "generated_on": today.isoformat(),
        "leads": leads,
        "tasks": tasks,
        "events": events,
    }
