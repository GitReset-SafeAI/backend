BEGIN;

CREATE TABLE public.incident_ai_analyses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    analysis_status text NOT NULL
        CHECK (analysis_status IN ('completed', 'needs_review')),
    "IncidentCategory" text CHECK ("IncidentCategory" IN (
        'UNWANTED_FOLLOWING',
        'VERBAL_SEXUAL_HARASSMENT',
        'UNWANTED_PHYSICAL_CONTACT',
        'THREATENING_BEHAVIOUR',
        'UNCOMFORTABLE_STARING',
        'OTHER',
        'UNCLEAR'
    )),
    "SafetyConcernCategory" text CHECK ("SafetyConcernCategory" IN (
        'POOR_STREET_LIGHTING',
        'ISOLATED_OR_DESERTED_ROAD',
        'UNSAFE_BUS_STOP',
        'FEELING_UNSAFE_WITHOUT_CONFIRMED_INCIDENT',
        'PREVIOUS_SAFETY_CONCERNS_AT_LOCATION',
        'OTHER',
        'UNCLEAR'
    )),
    reported_time text,
    immediate_danger_reported boolean,
    severity smallint CHECK (severity BETWEEN 0 AND 10),
    confidence double precision CHECK (confidence >= 0 AND confidence <= 1),
    extracted_details jsonb NOT NULL
        CHECK (jsonb_typeof(extracted_details) = 'object')
);

CREATE INDEX incident_ai_analyses_report_created_idx
    ON public.incident_ai_analyses (report_id, created_at DESC);

COMMIT;
