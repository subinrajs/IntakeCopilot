"""Turn scenarios into fully specified gold cases: seeded identities, dates and referrers.

Everything printed on a requisition comes from a `Requisition`, and the gold labels are
derived from the same object, so labels and documents can never disagree.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta

from faker import Faker

from intake.eval.gold import GoldCase, GoldFields
from intake.synth.scenarios import ALL, Scenario

SEED = 2026
GOLD_VERSION = "v1"
FIRST_DAY = date(2026, 9, 1)

REFERRING_CLINICS = (
    "Maple Grove Family Health Team",
    "Harbourview Medical Centre",
    "Credit Valley Orthopaedic Associates",
    "Port Credit Neurology Clinic",
    "Erindale Community Health Centre",
    "Lakeview Internal Medicine",
)


@dataclass(frozen=True)
class Requisition:
    case_key: str
    scenario: Scenario
    received: date
    surname: str
    given_name: str
    sex: str
    dob: date
    health_card: str  # "1234 567 890 AB"
    patient_phone: str
    patient_address: str
    referrer_given: str
    referrer_surname: str
    billing_number: str
    clinic: str
    clinic_phone: str
    clinic_fax: str
    egfr_date: date | None

    @property
    def referrer_name(self) -> str:
        return f"{self.referrer_given} {self.referrer_surname}"

    @property
    def health_card_last4(self) -> str:
        return self.health_card.replace(" ", "")[6:10]

    def gold_case(self) -> GoldCase:
        s = self.scenario
        fields = GoldFields(
            patient_name=f"{self.given_name} {self.surname}",
            dob=self.dob,
            health_card_last4=self.health_card_last4,
            referrer_name=self.referrer_name,
            referrer_billing_number=self.billing_number,
            modality=s.modality,
            body_part=s.exam,
            laterality=s.laterality,
            contrast_requested=s.contrast,
            clinical_indication=s.indication,
            relevant_history=list(s.history),
            allergies=list(s.allergies),
            egfr=s.egfr,
            egfr_date=self.egfr_date,
            medications_of_note=list(s.meds),
            physician_marked_urgent=s.urgent,
        )
        return GoldCase(
            case_key=self.case_key,
            file=f"pdfs/{self.case_key}.pdf",
            difficulty=s.difficulty,
            noise=s.noise,
            layout=s.layout,
            as_of=self.received,
            priority=s.priority,
            protocol_id=s.protocol_id,
            contrast_flags=list(s.flags),
            fields=fields,
            notes=s.notes,
        )


def _phone(rng: random.Random) -> str:
    return f"905-555-{rng.randint(0, 199):04d}"  # 555-01xx: reserved fictional numbers


def build_requisitions() -> list[Requisition]:
    rng = random.Random(SEED)
    fake = Faker("en_CA")
    fake.seed_instance(SEED)
    requisitions: list[Requisition] = []
    for index, scenario in enumerate(ALL, start=1):
        received = FIRST_DAY + timedelta(days=(index * 7) % 29)
        sex: str = scenario.sex if scenario.sex else rng.choice(("F", "M"))
        given = fake.first_name_female() if sex == "F" else fake.first_name_male()
        surname = fake.last_name()
        age_years = rng.randint(*scenario.age)
        dob = received - timedelta(days=age_years * 365 + rng.randint(0, 364))
        if scenario.dob_day_first:
            # Day of 12 or less, different from the month, so DD/MM and MM/DD both parse.
            dob = date(dob.year, 4, 3)
        digits = "".join(str(rng.randint(0, 9)) for _ in range(10))
        version = "".join(rng.choice("ABCDEFGHJKLMNPRSTVWXYZ") for _ in range(2))
        health_card = f"{digits[:4]} {digits[4:7]} {digits[7:]} {version}"
        egfr_date = (
            received - timedelta(days=scenario.egfr_age_days)
            if scenario.egfr is not None and scenario.egfr_age_days is not None
            else None
        )
        requisitions.append(
            Requisition(
                case_key=f"gold-{GOLD_VERSION}-{index:03d}",
                scenario=scenario,
                received=received,
                surname=surname,
                given_name=given,
                sex=sex,
                dob=dob,
                health_card=health_card,
                patient_phone=_phone(rng),
                patient_address=f"{fake.street_address()}, Mississauga, ON",
                referrer_given=fake.first_name(),
                referrer_surname=fake.last_name(),
                billing_number=f"{rng.randint(100000, 999999)}",
                clinic=rng.choice(REFERRING_CLINICS),
                clinic_phone=_phone(rng),
                clinic_fax=_phone(rng),
                egfr_date=egfr_date,
            )
        )
    return requisitions
