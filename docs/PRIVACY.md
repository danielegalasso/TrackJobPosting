# Privacy Policy

**Last updated: September 10, 2026**

This Privacy Policy describes Our policies and procedures on the collection,
use, and disclosure of Your information when You use the ACIDE-Watch Service
("Service", "Application", "Website") and informs You about Your privacy
rights and legal protections under the General Data Protection Regulation
(GDPR), the California Consumer Privacy Act (CCPA/CPRA), and applicable
international privacy frameworks.

We use Your Personal Data to provide, maintain, and enhance the Service,
including sending automated email alerts for job openings matching Your
profile and pivot interests. By using the Service, You agree to the collection
and use of information in accordance with this Privacy Policy.

> **Operator note.** ACIDE-Watch is self-hosted software. The "Company" below
> is whoever deploys this instance — in a single-user deployment that is You,
> and the data described here never leaves Your own host except where this
> document says it does (the OpenRouter gateway and Your configured SMTP
> relay). Deployers making the Service available to other people are the Data
> Controller for those people and should replace the contact details at the
> foot of this document.

---

## Interpretation and Definitions

### Interpretation

The words of which the initial letter is capitalized have meanings defined
under the following conditions. The following definitions shall have the same
meaning regardless of whether they appear in singular or in plural.

### Definitions

* **Account:** A unique record or authenticated access credential created for
  You to access our Service or specific features.
* **Affiliate:** An entity that controls, is controlled by, or is under common
  control with a party (50% or more ownership of shares or voting rights).
* **Company** (referred to as "the Company", "We", "Us", or "Our"): the
  operator of this ACIDE-Watch deployment. For the purpose of the GDPR, the
  Company is the Data Controller.
* **Data Controller:** The legal entity which alone or jointly determines the
  purposes and means of the processing of Personal Data under the GDPR.
* **Device:** Any device that can access the Service, such as a desktop
  computer, laptop, mobile phone, or tablet.
* **OpenRouter / LLM Gateways:** Third-party universal inference routing
  platforms used to evaluate publicly published job data against candidate
  profiles.
* **Personal Data:** Any information that relates to an identified or
  identifiable individual (including identifiers, contact email, and uploaded
  resume text).
* **Service:** The ACIDE-Watch web application and its associated background
  job indexing and alerting daemons.
* **Service Provider:** Any natural or legal person who processes data on
  behalf of the Company (e.g., OpenRouter, SMTP mail relays, cloud/local
  database providers).
* **Usage Data:** Data collected automatically through the execution or access
  of the Service (e.g., page duration, API response latency, search queries
  executed).
* **You:** The individual accessing or using the Service, or the company or
  legal entity on behalf of which such individual is accessing or using the
  Service.

---

## Types of Data Collected

### 1. Personal Data

While using Our Service (particularly when subscribing to alerts or uploading
candidate criteria), We may collect:

* Email address, used strictly for transmitting job alert digests and
  verification tokens.
* First name, last name, and contact details.
* Professional profile documents (Curriculum Vitae / Resume text uploaded via
  `.pdf`, `.md`, or `.txt`).
* Specified career pivot interests and role search criteria.

### 2. Usage Data

Usage Data is collected automatically when interacting with the interface,
including:

* Client IP address, browser type, and browser version.
* Filter selections (roles, companies, seniority, location, rate parameters).
* Diagnostic logs and error traces from the job inspection daemons.

### 3. Cookies and Tracking Technologies

We utilize session and local storage mechanisms to remember Your filter states,
UI theme preference, saved/bookmarked jobs, and authentication state. We do not
use third-party behavioral cross-site advertising cookies.

---

## Use of Your Personal Data

The Company processes Your Personal Data for the following operations:

1. **To provide and maintain the Service:** including applying search filters
   and rendering the dashboard.
2. **To evaluate Job Opportunities:** running structured semantic matches
   between Your uploaded CV, Your pivot interests, and public job postings via
   OpenRouter API endpoints.
3. **To deliver Scheduled Email Alerts:** dispatching email summaries of
   matching jobs meeting Your threshold to Your designated email address.
4. **To manage Your Requests & Bookmarks:** storing saved jobs and dismissed
   listings.
5. **To comply with legal obligations and prevent abuse:** enforcing rate
   limits and abuse prevention on our servers.

---

## Disclosure & Third-Party Processing

* **Inference Routing (OpenRouter):** job descriptions and profile text
  extracts are transmitted to OpenRouter (`https://openrouter.ai/api/v1`)
  solely for semantic matching and scoring. OpenRouter operates as an
  unbundled processor subject to their own data retention and privacy
  standards. Note that this transmission includes Your CV text; if that is not
  acceptable to You, do not upload a CV, and the Service will score postings
  against Your stated interests alone.
* **Email Transmission (SMTP Relay):** Your email address and rendered alert
  cards are processed by the configured SMTP server (e.g., Gmail, SendGrid,
  Postmark) strictly to effectuate delivery.
* **Legal Requirements:** We may disclose Your data if required by lawful
  court orders, subpoenas, or statutory compliance mandates.

We do not transmit Your data to any other third party. In particular, no
analytics, advertising, or tracking service receives it.

---

## Data Retention and Deletion Rights

* You may delete Your uploaded CV, saved jobs, and email alert subscriptions
  at any time directly through the Settings interface, through the
  one-click unsubscribe link at the foot of every alert email, or by deleting
  the local SQLite database (`data/acide_storage.db`).
* Resume extracts are retained locally on Your host environment and are purged
  immediately upon replacing or removing the CV file in the configuration
  panel. Replacing a CV deletes the previous file rather than archiving it.
* Deleting an alert subscription also deletes the record of which postings
  were sent to it.

---

## GDPR Data Subject Rights

If You reside in the European Economic Area (EEA), You maintain:

* The right to access, rectify, or erase Your Personal Data.
* The right to object to or restrict processing.
* The right to data portability (exporting saved opportunities and search
  criteria in JSON format, available from the API at `/api/jobs` and
  `/api/alerts`).
* The right to withdraw consent at any time without affecting prior lawful
  processing.

---

## CCPA/CPRA Privacy Notice (California Residents)

We do not sell Your personal data or consumer information as commonly
understood or defined under the California Consumer Privacy Act. We do not
share Your private CV or email data with data brokers. You possess the right to
know, delete, correct, and limit the use of sensitive personal information by
submitting an in-app deletion request.

---

## Contact Us

For any inquiries regarding this Privacy Policy or data handling, contact the
administrator email specified in this deployment's `setup.json`
(`admin_email`).
