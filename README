# SafeRoute AI 🛡️

**AI-powered harassment reporting, hotspot detection, and safety-aware route recommendations.**

SafeRoute AI is a mobile application concept designed to help address sexual harassment on public transport and surrounding travel routes in Sri Lanka. It aims to make incident reporting more accessible, identify patterns in reported incidents, and help users make more informed decisions when choosing walking routes.

## 🎯 Problem Statement

Sexual harassment is often underreported, making it difficult to understand where and when incidents occur. At the same time, people travelling alone may have limited information about the areas they pass through and the alternative routes available to them.

SafeRoute AI aims to address these challenges through accessible incident reporting, AI-assisted analysis, geographic hotspot detection, and safety-aware route recommendations.

## ✨ Planned Features

- **Multilingual Incident Reporting:** Submit harassment reports through text or voice in Sinhala and English, with support for additional languages planned for the future.
- **Singlish Understanding:** Interpret Sinhala written using English characters, including informal spelling variations and mixed-language messages.
- **AI-Powered Incident Classification:** Categorise submitted reports into standard incident types using a large language model (LLM).
- **AI-Assisted Severity Assessment:** Assign provisional severity labels based on report descriptions to support consistent incident organisation.
- **Anomaly Detection:** Identify potential duplicate reports, unusual submission patterns, and suspicious reporting activity for further review.
- **Harassment Hotspot Detection:** Analyse the geographic and temporal distribution of eligible reports to identify concentrations of reported incidents.
- **Interactive Hotspot Map:** Visualise aggregated incident patterns to support public awareness and help relevant authorities identify areas that may warrant further investigation.
- **Safety-Aware Route Recommendations:** Compare walking routes using reported-incident patterns and other available safety-related information.
- **Privacy-Conscious Data Handling:** Restrict access to identifying information and minimise the exposure and retention of sensitive report data.

## 🧠 AI and Analytics

SafeRoute AI plans to combine existing AI models with data-analysis techniques to support its core features:

- **Language understanding:** Interpret reports submitted in supported languages and informal writing styles.
- **Incident classification:** Extract structured incident categories and relevant details from unstructured descriptions.
- **Severity assessment:** Estimate provisional severity using defined criteria.
- **Anomaly detection:** Flag potentially suspicious reporting patterns for human review.
- **Spatial clustering:** Apply DBSCAN to identify geographic concentrations of reported incidents.
- **Safety-aware routing:** Incorporate available incident data and other relevant route attributes into route comparisons.

AI-generated classifications and anomaly flags are decision-support signals, not proof that a reported incident occurred or that a report is fraudulent.

## 🛠️ Proposed Technology Stack

| Component | Technology |
|---|---|
| Mobile application | React Native, Expo, TypeScript |
| Backend API | Python, FastAPI |
| Database | PostgreSQL, Supabase |
| Geospatial queries | PostGIS |
| Authentication and storage | Supabase Auth and Storage |
| AI integration | Multilingual LLM API |
| Voice transcription | Speech-to-text service supporting the selected languages |
| Hotspot detection | Python, scikit-learn, DBSCAN |
| Maps and routing | OpenStreetMap and a compatible routing service |
| Version control | Git and GitHub |

*The technology stack is proposed and may evolve during development.*

## 🔒 Privacy, Safety and Responsible AI

SafeRoute AI is intended to handle sensitive incident information responsibly.

- Protect reporter identities and restrict access to sensitive information.
- Avoid exposing personal identifiers through public APIs, maps, or hotspot analytics.
- Apply duplicate detection and abuse-prevention measures without automatically dismissing reports.
- Display aggregated incident information rather than publicly exposing precise incident details.
- Treat areas with few or no reports as areas with limited evidence, not necessarily as safe areas.
- Communicate the limitations and uncertainty of AI-generated assessments and route recommendations.
- Avoid presenting any recommended route as guaranteed to be safe.

## 🚧 Development Status

**Status:** Initial project planning

The application architecture, repository structure, and individual feature implementations will be established during development. Features will be prioritised according to feasibility, available data, and the project timeline.

## 🌱 Future Scope

Potential future enhancements include:

- Tamil and additional language support.
- Improved language understanding for code-switching and informal writing.
- Partnerships with relevant authorities and support organisations.
- More comprehensive and validated route-safety data.
- Enhanced moderation and reporting-integrity mechanisms.
- Evaluation of hotspot and route recommendations using reliable real-world data.

## ⚠️ Data and Evaluation Disclaimer

Hotspots represent concentrations of **reported incidents**, not a complete measure of actual harassment or objective danger. Reporting bias, inaccurate submissions, and missing data can affect the results.

Route recommendations are intended to support informed decisions and must not be interpreted as guarantees of personal safety.

## 🤝 Contributing

This project is being developed collaboratively. Contribution guidelines, development instructions, and project structure will be added as implementation progresses.

---

**SafeRoute AI — Making harassment patterns more visible and travel decisions more informed.**