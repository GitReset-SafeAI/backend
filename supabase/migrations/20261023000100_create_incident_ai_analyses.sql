BEGIN;

CREATE TABLE public.incident_ai_analyses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Reporting has no table yet. Add its FK and deletion policy when agreed.
    report_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    analysis_status text NOT NULL
        CHECK (analysis_status IN ('completed', 'needs_review')),
    category text NOT NULL CHECK (category IN (
        'unwanted_touching', 'sexual_remarks', 'stalking',
        'threatening_behaviour', 'other', 'unclear'
    )),
    severity text NOT NULL
        CHECK (severity IN ('low', 'moderate', 'high', 'critical', 'undetermined')),
    confidence double precision CHECK (confidence >= 0 AND confidence <= 1),
    extracted_details jsonb NOT NULL
        CHECK (jsonb_typeof(extracted_details) = 'object'),
    review_reason text CHECK (length(btrim(review_reason)) > 0),
    CONSTRAINT incident_ai_analysis_outcome CHECK (
        (analysis_status = 'completed'
            AND category <> 'unclear' AND severity <> 'undetermined'
            AND confidence IS NOT NULL AND review_reason IS NULL)
        OR
        (analysis_status = 'needs_review'
            AND category = 'unclear' AND severity = 'undetermined'
            AND review_reason IS NOT NULL)
    )
);

CREATE INDEX incident_ai_analyses_report_created_idx
    ON public.incident_ai_analyses (report_id, created_at DESC);

COMMIT;
