# Moodle Web Services API Documentation — UTH Elearning

Trạng thái: tài liệu tham chiếu API hiện hành; mọi payload mẫu phải được khử
token, cookie và dữ liệu định danh trước khi commit.

> **System**: UTH Elearning (courses.ut.edu.vn)
> **Moodle Version**: 4.3.5 (Build: 20240610) — Internal version `2023100905`
> **Theme**: Edly
> **Total Available WS Functions**: 421 (via `moodle_mobile_app` service)
> **Last Updated**: 2026-06-22
> **Purpose**: Complete API reference for the UTHelper project — designed so any AI agent can read this file and interact with the Moodle APIs immediately.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Authentication](#2-authentication)
3. [Site Information](#3-site-information)
4. [User Profile](#4-user-profile)
5. [Enrolled Courses](#5-enrolled-courses)
6. [Grades — Overview](#6-grades--overview)
7. [Grades — Detailed Items (Per Course)](#7-grades--detailed-items-per-course)
8. [Assignments](#8-assignments)
    - 8a. [Submission Status](#mod_assign_get_submission_status)
    - 8b. [Quiz](#8b-quiz)
9. [Calendar / Upcoming Events](#9-calendar--upcoming-events)
    - 9a. [Action Events by Time Range](#core_calendar_get_action_events_by_timesort)
    - 9b. [Course Updates Since](#core_course_get_updates_since)
10. [Course Contents (Sections & Modules)](#10-course-contents-sections--modules)
11. [Activity Completion Status](#11-activity-completion-status)
12. [Notifications](#12-notifications)
    - 12b. [Known Limitations & Disabled Endpoints](#12b-known-limitations--disabled-endpoints)
13. [Full Function List (All 421)](#13-full-function-list-all-421)
14. [Portal API vs Moodle WS — Comparison Table](#14-portal-api-vs-moodle-ws--comparison-table)
15. [Feature Priority Table for UTHelper](#15-feature-priority-table-for-uthelper)
16. [Technical Reference](#16-technical-reference)

---

## 1. System Overview

| Property | Value |
|---|---|
| **Base URL** | `https://courses.ut.edu.vn` |
| **Moodle Version** | 4.3.5 (Build: 20240610) |
| **Internal Version** | `2023100905` |
| **Theme** | Edly |
| **WS Service** | `moodle_mobile_app` |
| **Total Functions** | 421 |
| **Auth Method** | Token-based (persistent, no session expiry) |
| **Request Format** | GET or POST to REST endpoint |
| **Response Format** | JSON (`moodlewsrestformat=json`) |

### Base Request Pattern

All API calls (except authentication) follow this pattern:

```
GET/POST https://courses.ut.edu.vn/webservice/rest/server.php?wstoken={TOKEN}&wsfunction={FUNCTION_NAME}&moodlewsrestformat=json
```

Additional parameters are appended as query string parameters (GET) or form-encoded body (POST).

---

## 2. Authentication

### `GET /login/token.php`

Obtain a persistent web service token. This token does **not** expire with the session and remains valid until manually revoked or the password changes.

#### Request

```
GET https://courses.ut.edu.vn/login/token.php?username={USERNAME}&password={PASSWORD}&service=moodle_mobile_app
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `username` | string | ✅ | Student ID (e.g., `STUDENT_ID`) |
| `password` | string | ✅ | URL-encoded password (e.g., `YOUR_PASSWORD_URLENCODED`) |
| `service` | string | ✅ | Must be `moodle_mobile_app` — gives access to all 421 functions |

#### Response (200 OK — Success)

```json
{
  "token": "YOUR_TOKEN_HERE",
  "privatetoken": "REDACTED_PRIVATE_TOKEN"
}
```

| Field | Type | Description |
|---|---|---|
| `token` | string | 32-char hex token used in all subsequent API calls as `wstoken` |
| `privatetoken` | string | Private token for auto-login — not needed for API calls |

#### Response (200 OK — Error)

```json
{
  "error": "Invalid login, please try again",
  "errorcode": "invalidlogin",
  "stacktrace": null,
  "debuginfo": null,
  "reproductionlink": null
}
```

> [!IMPORTANT]
> - **Token is PERSISTENT** — it does not expire with the browser session. Store it securely.
> - **No reCAPTCHA** is required on this endpoint.
> - **Auth provider**: `uth` (custom UTH auth plugin, not standard Moodle auth).
> - The `service` value `moodle_mobile_app` unlocks **421 functions**. Other service names may have fewer functions.

---

## 3. Site Information

### `core_webservice_get_site_info`

Retrieve metadata about the Moodle site and the authenticated user. This is typically the first call after authentication to verify the token and get the `userid`.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=core_webservice_get_site_info&moodlewsrestformat=json
```

No additional parameters required.

#### Response (200 OK)

```json
{
  "sitename": "Elearning",
  "username": "STUDENT_ID",
  "firstname": "Nguyễn Văn",
  "lastname": "A",
  "fullname": "Nguyễn Văn A",
  "lang": "vi",
  "userid": 11763,
  "siteurl": "https://courses.ut.edu.vn",
  "userpictureurl": "https://courses.ut.edu.vn/theme/image.php/edly/core/1782075603/u/f1",
  "functions": [
    {"name": "core_webservice_get_site_info", "version": "2023100905"},
    {"name": "core_enrol_get_users_courses", "version": "2023100905"}
  ],
  "downloadfiles": 1,
  "uploadfiles": 1,
  "release": "4.3.5 (Build: 20240610)",
  "version": "2023100905",
  "mobilecssurl": "",
  "advancedfeatures": [
    {"name": "usecomments", "value": 1},
    {"name": "usetags", "value": 1},
    {"name": "enablenotes", "value": 1},
    {"name": "enableblogs", "value": 1}
  ],
  "usercanmanageownfiles": true,
  "userquota": 0,
  "usermaxuploadfilesize": -1,
  "userhomepage": 0,
  "userprivateaccesskey": "...",
  "siteid": 1,
  "sitecalendartype": "gregorian",
  "usercalendartype": "gregorian",
  "userissiteadmin": false,
  "theme": "edly"
}
```

| Field | Type | Description |
|---|---|---|
| `userid` | int | **Critical** — needed for most subsequent API calls |
| `username` | string | Student ID / login name |
| `fullname` | string | Display name of the authenticated user |
| `siteurl` | string | Base URL of the Moodle instance |
| `release` | string | Moodle version string |
| `version` | string | Internal Moodle version number |
| `functions` | array | List of all available WS functions (421 entries) |
| `theme` | string | Active Moodle theme (`edly`) |

---

## 4. User Profile

### `core_user_get_users_by_field`

Retrieve detailed user profile information.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=core_user_get_users_by_field&field=id&values[0]=11763&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `field` | string | ✅ | Field to search by: `id`, `username`, `email`, `idnumber` |
| `values[0]` | string | ✅ | Value to search for (supports multiple: `values[1]`, etc.) |

#### Response (200 OK)

```json
[
  {
    "id": 11763,
    "username": "STUDENT_ID",
    "fullname": "Nguyễn Văn A",
    "firstname": "Nguyễn Văn",
    "lastname": "A",
    "email": "student@example.com",
    "department": "",
    "institution": "",
    "idnumber": "100625",
    "firstaccess": 1725440727,
    "lastaccess": 1782128019,
    "auth": "uth",
    "suspended": false,
    "confirmed": true,
    "lang": "vi",
    "country": "VN",
    "city": "",
    "description": "",
    "descriptionformat": 1,
    "profileimageurlsmall": "https://courses.ut.edu.vn/theme/image.php/edly/core/1782075603/u/f2",
    "profileimageurl": "https://courses.ut.edu.vn/theme/image.php/edly/core/1782075603/u/f1",
    "preferences": [
      {"name": "login_failed_count_since_success", "value": "134"},
      {"name": "auth_forcepasswordchange", "value": "0"},
      {"name": "_lastloaded", "value": "1782128019"}
    ]
  }
]
```

| Field | Type | Description |
|---|---|---|
| `id` | int | Moodle internal user ID |
| `username` | string | Login username (student ID) |
| `fullname` | string | Full display name |
| `email` | string | Registered email address |
| `idnumber` | string | External ID (e.g., `100625`) |
| `firstaccess` | int | Unix timestamp of first login |
| `lastaccess` | int | Unix timestamp of most recent access |
| `auth` | string | Auth plugin (`uth` = custom UTH auth) |
| `suspended` | bool | Whether account is suspended |
| `confirmed` | bool | Whether account is confirmed |
| `lang` | string | Preferred language (`vi` = Vietnamese) |
| `country` | string | Country code (`VN`) |
| `profileimageurl` | string | Full-size profile image URL |
| `profileimageurlsmall` | string | Thumbnail profile image URL |
| `preferences` | array | User preference key-value pairs |

> [!NOTE]
> The response is an **array** even when querying a single user. Always access `response[0]`.

---

## 5. Enrolled Courses

### `core_enrol_get_users_courses`

Retrieve all courses the user is enrolled in.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=core_enrol_get_users_courses&userid=11763&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `userid` | int | ✅ | User ID from `core_webservice_get_site_info` |

#### Response (200 OK)

Returns an **array** of course objects. Example showing 2 of 37 total courses:

```json
[
  {
    "id": 21252,
    "shortname": "[TT]_HKII2025-2026_Lập trình Web_012012103107",
    "fullname": "[012012103107] - Lập trình Web - 7480102109360",
    "displayname": "[012012103107] - Lập trình Web - 7480102109360",
    "enrolledusercount": 68,
    "idnumber": "75012012103107",
    "visible": 1,
    "format": "topics",
    "showgrades": true,
    "lang": "",
    "enablecompletion": true,
    "completionhascriteria": true,
    "completionusertracked": true,
    "category": 512,
    "progress": 6.666666666666667,
    "completed": false,
    "startdate": 1771995600,
    "enddate": 1782363600,
    "marker": 0,
    "lastaccess": 1782046370,
    "isfavourite": false,
    "hidden": false,
    "overviewfiles": [],
    "showactivitydates": true,
    "showcompletionconditions": true,
    "timemodified": 1778336591
  },
  {
    "id": 21129,
    "shortname": "[TT]_HKII2025-2026_An toàn thông tin_012012303318",
    "fullname": "[012012303318] - An toàn thông tin - 7480201190360",
    "displayname": "[012012303318] - An toàn thông tin - 7480201190360",
    "enrolledusercount": 67,
    "idnumber": "75012012303318",
    "visible": 1,
    "format": "topics",
    "showgrades": true,
    "enablecompletion": true,
    "category": 512,
    "progress": 20,
    "completed": false,
    "startdate": 1771995604,
    "enddate": 1782363604,
    "lastaccess": 1778507679,
    "isfavourite": false,
    "hidden": false,
    "showactivitydates": true,
    "showcompletionconditions": true,
    "timemodified": 1778336591
  }
]
```

#### Field Reference

| Field | Type | Description |
|---|---|---|
| `id` | int | **Course ID** — use this in all course-specific API calls |
| `shortname` | string | Short identifier; format: `[TT]_HKII2025-2026_{CourseName}_{CourseCode}` |
| `fullname` | string | Full display name; format: `[{CourseCode}] - {CourseName} - {ClassCode}` |
| `displayname` | string | Same as `fullname` in most cases |
| `enrolledusercount` | int | Number of students enrolled |
| `idnumber` | string | External ID. Prefix encodes semester: `75` = HKII 2025-2026 |
| `visible` | int | `1` = visible to students, `0` = hidden by admin |
| `format` | string | Course format: `topics`, `weeks`, `social`, etc. |
| `showgrades` | bool | Whether grades are visible to students |
| `enablecompletion` | bool | Whether activity completion tracking is enabled |
| `category` | int | Category ID the course belongs to |
| `progress` | float | Completion progress as percentage (0–100). `null` if tracking disabled |
| `completed` | bool | Whether the user completed all course criteria |
| `startdate` | int | Course start date (Unix timestamp) |
| `enddate` | int | Course end date (Unix timestamp). `0` = no end date |
| `lastaccess` | int | User's last access to this course (Unix timestamp). `0` = never |
| `isfavourite` | bool | Whether the user marked this as a favourite |
| `hidden` | bool | Whether the **user** has hidden this course (personal preference) |
| `timemodified` | int | Last modification timestamp |
| `showactivitydates` | bool | Whether activity dates are shown |
| `showcompletionconditions` | bool | Whether completion conditions are displayed |

> [!TIP]
> **Semester decoding from `idnumber`**: The first 2 digits of `idnumber` indicate the semester.
> - `75` → HK II 2025-2026
> - `74` → HK I 2025-2026
> - Pattern: The prefix is a semester sequence number used internally by UTH.

> [!NOTE]
> Total of **37 courses** returned for this user. This includes current and past semesters. Filter by `startdate`/`enddate` or `idnumber` prefix to get only the current semester's courses.

---

## 6. Grades — Overview

### `gradereport_overview_get_course_grades`

Get a summary of the user's grade for each enrolled course. This returns the final/total grade per course, not individual grade items.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=gradereport_overview_get_course_grades&userid=11763&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `userid` | int | ✅ | User ID |

#### Response (200 OK)

```json
{
  "grades": [
    {"courseid": 21129, "grade": "9.3", "rawgrade": "9.28216"},
    {"courseid": 21252, "grade": "10.0", "rawgrade": "10.00000"},
    {"courseid": 21008, "grade": "9.0", "rawgrade": "8.97525"},
    {"courseid": 8389, "grade": "232.8", "rawgrade": "232.81667"},
    {"courseid": 1256, "grade": "19.0", "rawgrade": "19.00000"},
    {"courseid": 6054, "grade": "2.5", "rawgrade": "2.50000"},
    {"courseid": 14647, "grade": "50.0", "rawgrade": "50.00000"},
    {"courseid": 14413, "grade": "9.0", "rawgrade": "9.00000"},
    {"courseid": 8347, "grade": "28.2", "rawgrade": "28.22222"},
    {"courseid": 8408, "grade": "9.8", "rawgrade": "9.80000"},
    {"courseid": 13424, "grade": "46.0", "rawgrade": "46.00000"},
    {"courseid": 26815, "grade": "-", "rawgrade": null}
  ],
  "warnings": []
}
```

| Field | Type | Description |
|---|---|---|
| `grades` | array | Array of grade objects |
| `grades[].courseid` | int | Course ID (matches `id` from enrolled courses) |
| `grades[].grade` | string | Formatted display grade. `"-"` means no grade yet |
| `grades[].rawgrade` | string\|null | Raw numeric grade with full precision. `null` = no grade |
| `warnings` | array | Any warnings (usually empty) |

> [!WARNING]
> **Grade scales vary per course!** Some courses use a 0–10 scale, others 0–100, 0–220, etc. The grade value depends on the course's grading configuration. Always check the grade max from `gradereport_user_get_grade_items` for the `itemtype=course` entry.

> [!NOTE]
> - `grade = "-"` and `rawgrade = null` → The course has **no grade** calculated yet.
> - Not all enrolled courses may appear in this list — only courses with a gradebook entry.

---

## 7. Grades — Detailed Items (Per Course)

### `gradereport_user_get_grade_items`

Get the full grade breakdown for a specific course, including individual assignment grades, quiz grades, attendance scores, and the course total.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=gradereport_user_get_grade_items&courseid=21129&userid=11763&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `courseid` | int | ✅ | Course ID |
| `userid` | int | ✅ | User ID |

#### Response (200 OK)

Example for course **An toàn thông tin** (`courseid=21129`):

```json
{
  "usergrades": [
    {
      "courseid": 21129,
      "courseidnumber": "75012012303318",
      "userid": 11763,
      "userfullname": "Nguyễn Văn A",
      "useridnumber": "100625",
      "maxdepth": 1,
      "gradeitems": [
        {
          "id": 222675,
          "itemname": "Attendance (Điểm danh)",
          "itemtype": "mod",
          "itemmodule": "attendance",
          "iteminstance": 7626,
          "itemnumber": 0,
          "idnumber": "CC",
          "categoryid": 8269,
          "outcomeid": null,
          "scaleid": null,
          "locked": false,
          "cmid": 551308,
          "weightraw": 0.125,
          "weightformatted": "12.50 %",
          "graderaw": 10,
          "gradedatesubmitted": null,
          "gradedategraded": 1781884862,
          "gradehiddenbydate": false,
          "gradeneedsupdate": false,
          "gradeishidden": false,
          "gradeisoverridden": false,
          "gradeformatted": "10.0",
          "grademin": 0,
          "grademax": 10,
          "rangeformatted": "0&ndash;10",
          "percentageformatted": "100.0 %",
          "feedback": "",
          "feedbackformat": 1
        },
        {
          "id": 222677,
          "itemname": "Quiz 1",
          "itemtype": "mod",
          "itemmodule": "quiz",
          "iteminstance": 5660,
          "itemnumber": 0,
          "idnumber": "KT2",
          "categoryid": 8269,
          "weightraw": 0.125,
          "weightformatted": "12.50 %",
          "graderaw": 9.66667,
          "gradeformatted": "9.7",
          "grademin": 0,
          "grademax": 10,
          "percentageformatted": "96.7 %"
        },
        {
          "id": 222685,
          "itemname": "Thi hết môn",
          "itemtype": "mod",
          "itemmodule": "quiz",
          "iteminstance": 5668,
          "itemnumber": 0,
          "idnumber": "CK",
          "categoryid": 8269,
          "weightraw": 0.125,
          "weightformatted": "12.50 %",
          "graderaw": 9.8,
          "gradeformatted": "9.8",
          "grademin": 0,
          "grademax": 10,
          "percentageformatted": "98.0 %"
        },
        {
          "id": 207537,
          "itemname": null,
          "itemtype": "course",
          "itemmodule": null,
          "itemnumber": null,
          "idnumber": null,
          "categoryid": null,
          "weightraw": null,
          "weightformatted": "&nbsp;",
          "graderaw": 9.28216,
          "gradeformatted": "9.3",
          "grademin": 0,
          "grademax": 220,
          "rangeformatted": "0&ndash;220",
          "percentageformatted": "4.2 %"
        }
      ]
    }
  ],
  "warnings": []
}
```

#### Grade Item Field Reference

| Field | Type | Description |
|---|---|---|
| `id` | int | Grade item ID |
| `itemname` | string\|null | Name of the graded item. `null` for the course total |
| `itemtype` | string | `"mod"` = activity grade, `"course"` = **course total** |
| `itemmodule` | string\|null | Activity type: `quiz`, `assign`, `attendance`, `forum`, etc. |
| `idnumber` | string\|null | Grade code — see table below |
| `weightraw` | float\|null | Weight as a decimal (e.g., `0.125` = 12.5%) |
| `weightformatted` | string | Weight as formatted percentage |
| `graderaw` | float\|null | Raw numeric grade. `null` = not yet graded |
| `gradeformatted` | string | Formatted display grade. `"-"` = not graded |
| `grademin` | float | Minimum possible grade |
| `grademax` | float | Maximum possible grade |
| `percentageformatted` | string | Grade as percentage of the range |
| `gradedategraded` | int\|null | When the grade was last updated (Unix timestamp) |
| `cmid` | int | Course module ID (matches `modules[].id` in course contents) |
| `locked` | bool | Whether the grade is locked |
| `gradeishidden` | bool | Whether the grade is hidden from students |
| `gradeisoverridden` | bool | Whether the grade was manually overridden |
| `feedback` | string | Grader's feedback text |

#### UTH Grade Code Reference (`idnumber` field)

| Code | Vietnamese | English | Meaning |
|---|---|---|---|
| `CC` | Chuyên cần | Attendance | Attendance/participation score |
| `KT`, `KT1`, `KT2` | Kiểm tra | Quiz/Test | In-semester quiz or test |
| `GK` | Giữa kỳ | Midterm | Midterm exam |
| `CK` | Cuối kỳ | Final | Final exam |
| `QT` | Quá trình | Process | Continuous assessment / coursework |
| `TH` | Thực hành | Practice | Lab/practical work |
| `BTL` | Bài tập lớn | Project | Major assignment/project |

> [!IMPORTANT]
> The entry with `itemtype = "course"` is the **total course grade**. It has `itemname = null` and `itemmodule = null`. Its `grademax` tells you the course's maximum possible grade (e.g., 220 in this example, not necessarily 10).

---

## 8. Assignments

### `mod_assign_get_assignments`

Retrieve assignment details for one or more courses.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=mod_assign_get_assignments&courseids[0]=21252&courseids[1]=21263&courseids[2]=21129&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `courseids[0]` | int | ✅ | First course ID |
| `courseids[1]` | int | ❌ | Second course ID (optional, add more with `[2]`, `[3]`...) |

#### Response (200 OK)

Example showing assignments from multiple courses (42 total across 6 courses queried):

```json
{
  "courses": [
    {
      "id": 21252,
      "fullname": "[012012103107] - Lập trình Web - 7480102109360",
      "shortname": "[TT]_HKII2025-2026_Lập trình Web_012012103107",
      "timemodified": 1778336591,
      "assignments": [
        {
          "id": 114748,
          "cmid": 640234,
          "course": 21252,
          "name": "Nộp bài nhóm cuối kỳ",
          "nosubmissions": 0,
          "submissiondrafts": 0,
          "sendnotifications": 0,
          "sendlatenotifications": 0,
          "sendstudentnotifications": 1,
          "duedate": 1783270740,
          "allowsubmissionsfromdate": 1778691600,
          "grade": 10,
          "timemodified": 1778691739,
          "completionsubmit": 1,
          "cutoffdate": 0,
          "gradingduedate": 0,
          "teamsubmission": 0,
          "requireallteammemberssubmit": 0,
          "blindmarking": 0,
          "hidegrader": 0,
          "markingworkflow": 0,
          "markingallocation": 0,
          "requiresubmissionstatement": 0,
          "preventsubmissionnotingroup": 0,
          "submissionstatement": "",
          "configs": [],
          "intro": "",
          "introformat": 1,
          "introfiles": [],
          "introattachments": []
        },
        {
          "id": 118028,
          "cmid": 656402,
          "course": 21252,
          "name": "Bài tập cá nhân phần HTML + CSS",
          "duedate": 1781456340,
          "grade": 10,
          "timemodified": 1780852405,
          "completionsubmit": 1,
          "cutoffdate": 0,
          "intro": "<p>E-learning 01+02</p>",
          "introformat": 1
        }
      ]
    },
    {
      "id": 21263,
      "fullname": "[012012213606] - Lập trình Java - 7480102109360",
      "assignments": [
        {
          "id": 99766,
          "cmid": 607040,
          "course": 21263,
          "name": "Đăng ký thành viên nhóm",
          "duedate": 1772865900,
          "grade": 10,
          "cutoffdate": 1772865900,
          "intro": "<p>Nhóm 4-5 sinh viên</p><p><strong>Nhóm trưởng</strong> đại diện gửi danh sách nhóm và link git</p>",
          "introformat": 1
        }
      ]
    }
  ],
  "warnings": []
}
```

#### Assignment Field Reference

| Field | Type | Description |
|---|---|---|
| `id` | int | Assignment ID |
| `cmid` | int | Course module ID |
| `course` | int | Course ID this assignment belongs to |
| `name` | string | Assignment title |
| `duedate` | int | Due date (Unix timestamp). **`0` = no due date** |
| `cutoffdate` | int | Hard cutoff — no submissions after this. **`0` = no cutoff** |
| `allowsubmissionsfromdate` | int | Earliest submission date (Unix timestamp). `0` = any time |
| `grade` | int | Maximum grade (e.g., `10`) |
| `nosubmissions` | int | `1` = no file submission (offline assignment) |
| `submissiondrafts` | int | `1` = students can save drafts before final submit |
| `completionsubmit` | int | `1` = submission counts as activity completion |
| `teamsubmission` | int | `1` = group/team submission enabled |
| `blindmarking` | int | `1` = anonymous grading enabled |
| `intro` | string | Assignment description (HTML) |
| `introformat` | int | Format: `1`=HTML, `0`=MOODLE, `2`=PLAIN, `4`=MARKDOWN |
| `timemodified` | int | Last modification timestamp |

> [!TIP]
> - Compare `duedate` against `Date.now() / 1000` to find **overdue** assignments.
> - If `cutoffdate > 0` and `cutoffdate < now`, **no more submissions are accepted**.
> - Use `mod_assign_get_submission_status` with `assignid` to check if the user has already submitted.

### `mod_assign_get_submission_status`

Check whether a user has submitted an assignment, and get submission details.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=mod_assign_get_submission_status&assignid=112407&userid=11763&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `assignid` | int | ✅ | Assignment ID (from `mod_assign_get_assignments`) |
| `userid` | int | ❌ | User ID (defaults to current user) |

#### Response (200 OK)

```json
{
  "lastattempt": {
    "teamsubmission": {
      "id": 4480268,
      "userid": 0,
      "attemptnumber": 0,
      "timecreated": 1778730937,
      "timemodified": 1778730937,
      "status": "new",
      "groupid": 80002,
      "assignment": 112407,
      "latest": 1,
      "plugins": [
        {"type": "file", "name": "File submissions", "fileareas": [{"area": "submission_files", "files": []}]},
        {"type": "comments", "name": "Submission comments"}
      ]
    },
    "submissiongroup": 80002,
    "submissionsenabled": true,
    "locked": false,
    "graded": false,
    "canedit": false,
    "caneditowner": false,
    "cansubmit": false,
    "extensionduedate": null,
    "timelimit": 0,
    "blindmarking": false,
    "gradingstatus": "notgraded",
    "usergroups": [80002]
  },
  "assignmentdata": {
    "attachments": {
      "intro": [],
      "activity": [
        {
          "filename": "Screenshot 2026-03-05 143648.png",
          "filesize": 6184,
          "fileurl": "https://courses.ut.edu.vn/webservice/pluginfile.php/1221015/mod_assign/activityattachment/0/Screenshot%202026-03-05%20143648.png",
          "mimetype": "image/png"
        }
      ]
    },
    "activity": "<p>Groups have to submit the final version for lecturer.</p>"
  }
}
```

| Field | Type | Description |
|---|---|---|
| `lastattempt.teamsubmission` | object | Team submission details (when `teamsubmission=1`) |
| `lastattempt.teamsubmission.status` | string | `"new"`, `"draft"`, `"submitted"`, `"reopened"` |
| `lastattempt.submissiongroup` | int | Group ID for team submission |
| `lastattempt.submissionsenabled` | bool | Whether submissions are enabled |
| `lastattempt.graded` | bool | Whether the submission has been graded |
| `lastattempt.canedit` | bool | Whether user can edit |
| `lastattempt.cansubmit` | bool | Whether user can submit |
| `lastattempt.gradingstatus` | string | `"notgraded"`, `"graded"` |
| `assignmentdata.attachments` | object | Attached files from teacher |

> [!TIP]
> - `status = "new"` means **no submission yet**.
> - `status = "submitted"` means the user has submitted.
> - If `canedit = false` and `cansubmit = false`, the submission window is closed.

### Verified assignment-file workflow and draft semantics

The upload endpoint stores each multipart file in the current user's draft area.
Allocate an unused draft item ID first, then pass that same positive `itemid` to
each sequential upload that belongs to one logical file set. The response identity
(`itemid`, normalized `filepath`, and `filename`) is authoritative. Uploading an
already-present path/name must return the structured `filenameexist` error; a
generic failed upload is not sufficient evidence of duplicate handling. It is not
an overwrite operation. Depending on the endpoint path, Moodle may return that
explicit `errorcode` directly or inside the first object of the normal JSON list
envelope. The UTH production `upload.php` response instead exposes the short
structured code in `errortype`; clients may accept either explicitly named code
field in a direct or list-wrapped error object. They must preserve the exact
code-like value and must not inspect or infer from free-form `error`, `message`,
`filename`, `filepath`, or `size` fields.

`mod_assign_save_submission` treats the supplied file-manager draft item as the
complete replacement set. Consequently, add, partial remove, rename, path-move,
and replace operations rebuild the exact desired set in a fresh draft and save it
as one state transition. Existing files that must remain are downloaded, bounded
by the assignment limits, and re-uploaded. Online text is preserved from the same
fresh status snapshot. Moodle 4.3 reports the file limit under
`maxfilesubmissions` (plural); older aliases remain accepted by the parser.

UTH production rejects an empty file-manager save for a file-only submission with
the structured warning `couldnotsavesubmission` ("Could not save submission").
Deleting the final file therefore follows Moodle's authenticated web confirmation
flow: open `action=removesubmissionconfirm`, parse the same-origin POST form, submit
`action=removesubmission` with its server-issued `userid` and `sesskey`, then verify
through `mod_assign_get_submission_status` that the file set is empty and status is
`new` or `reopened`. Before the destructive POST, the form `userid` must match a
fresh `core_webservice_get_site_info.userid` from the active WS token, and the
assignment fingerprint and safety policy are re-read once more. This uses a
short-lived cookie session and is required because
the site's student web-service contract does not expose
`mod_assign_remove_submission`. If online text is present, the workflow instead
keeps using an empty file draft so that deleting files does not delete that text.

Assignments with `submissiondrafts=1` remain in `draft` after save. They move to
`submitted` only through the separate `mod_assign_submit_for_grading` transition,
with explicit submission-statement acceptance when required. Assignments without
drafts may become final as part of save, so they are excluded from reversible live
write probes. Any non-empty Moodle `warnings` array makes save/finalization a
failure, even when the transport returned HTTP success. The client refreshes status
after every accepted transition and reports success only when the server file set
and state match exactly.

#### Opt-in live safety contract

`tests/test_submission_live_safe.py` is skipped unless
`UTH_LIVE_SUBMISSION_TEST=1`. There are exactly two accepted authentication paths:

1. A complete `UTH_TEST_USER` plus `UTH_TEST_PASS` pair. This path takes precedence
   over every cached app token, acquires a token without consulting global
   settings, verifies the returned account identity, and keeps the token in memory
   without calling `save_settings` or writing keyring.
2. If the environment pair is absent, a token read directly from secure keyring.
   The harness verifies its site-info username against the configured expected
   username before any probe.

A token restored from plaintext settings JSON is rejected for live assignment
mutation, even if the normal app compatibility loader accepted it. An incomplete
environment pair, missing keyring backend, missing expected username, or identity
mismatch skips before mutation. Never put credential values, tokens, authenticated
URLs, user or assignment identities, or file content in the command, test source,
logs, or test report.

The unlinked-draft probe allocates one unused item ID, uploads two unique synthetic
files to it, appends a third using the same ID, verifies returned and listed
identities, verifies a deliberate duplicate is rejected, and deletes only those
tracked identities in `finally`. It passes only after listing proves none remains,
and it is forbidden from calling either assignment mutation endpoint.

The optional assignment probe discovers candidates read-only and selects at most
one. Immediately before save it must freshly confirm all of the following:

- status is exactly `new`, with no remote file or online-text content;
- submissions are enabled and `canedit=true`, `locked=false`, `graded=false`;
- a file plugin is enabled, draft/repeated editing is enabled, and a small `.txt`
  file satisfies the advertised constraints;
- it is not a team submission and is already open;
- every non-zero due or cutoff boundary remains at least seven days away. A zero
  boundary means Moodle configured no boundary and is safe for this check.

The precheck fingerprint includes status/editability/lock/grade flags, remote file
metadata, a hash (never the value) of online text, draft and file-plugin enablement,
file count/size/type limits, team mode, and opening/due/cutoff timestamps. The
production workflow reloads assignment configuration and status, compares that
complete fingerprint, and evaluates the full live-safety predicate again before
any retained-file download, draft allocation, upload, or assignment save. Online
text appearing or draft mode becoming final during that interval therefore aborts
with zero file I/O and zero save calls.

The probe never calls `mod_assign_submit_for_grading`. Before cleanup it refreshes
again and clears only when the state is still editable/unlocked/ungraded and the
remote set is either empty or exactly the one generated identity. Its `finally`
path may retry only that same idempotent clear after another fresh safety check.
Absence of the exact generated path/name must be verified. An empty `draft`
submission record may remain because Moodle exposes no safe removal operation; this
is the sole allowed production residual and the run reports it explicitly. Any
state drift aborts without touching another file.

Run the opt-in module in a separate process with a hard 180-second deadline:

```powershell
$job = Start-Job { Set-Location $using:PWD; $env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_live_safe.py -q --tb=short -x }
if (-not (Wait-Job $job -Timeout 180)) { Stop-Job $job; Remove-Job $job -Force; throw 'Live draft probe timed out after 180 seconds' }
Receive-Job $job
Remove-Job $job
```

Every HTTP operation also has a finite connection/read timeout. If the opt-in flag,
secure authentication, required Moodle functions, or a qualifying assignment is
absent, the relevant probe skips without weakening any gate.

---

## 8b. Quiz

### `mod_quiz_get_quizzes_by_courses`

Get quiz details for one or more courses.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=mod_quiz_get_quizzes_by_courses&courseids[0]=21129&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `courseids[0]` | int | ✅ | Course ID |

#### Response (200 OK)

```json
{
  "quizzes": [
    {
      "id": 71353,
      "coursemodule": 551309,
      "course": 21129,
      "name": "Quiz 1",
      "intro": "",
      "timelimit": 900,
      "preferredbehaviour": "deferredfeedback",
      "attempts": 1,
      "grademethod": 1,
      "decimalpoints": 2,
      "sumgrades": 30,
      "grade": 10,
      "hasfeedback": 0,
      "section": 3,
      "visible": 1,
      "hasquestions": 1
    }
  ],
  "warnings": []
}
```

| Field | Type | Description |
|---|---|---|
| `id` | int | Quiz ID — use in `mod_quiz_get_user_attempts` |
| `coursemodule` | int | Course module ID (cmid) |
| `timelimit` | int | Time limit in **seconds** (`0` = no limit). E.g., `900` = 15 min |
| `attempts` | int | Max attempts allowed (`0` = unlimited) |
| `grademethod` | int | `1`=highest, `2`=average, `3`=first, `4`=last |
| `sumgrades` | float | Sum of all question grades |
| `grade` | float | Maximum grade (scaled). E.g., `10` |

### `mod_quiz_get_user_attempts`

Get a user's quiz attempts.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=mod_quiz_get_user_attempts&quizid=71353&userid=11763&status=finished&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `quizid` | int | ✅ | Quiz ID |
| `userid` | int | ✅ | User ID |
| `status` | string | ❌ | `"finished"`, `"inprogress"`, `"all"` (default: `"finished"`) |

#### Response (200 OK)

```json
{
  "attempts": [
    {
      "id": 8373960,
      "quiz": 71353,
      "userid": 11763,
      "attempt": 1,
      "uniqueid": 9048411,
      "layout": "1,2,3,4,5,...,30,0",
      "currentpage": 0,
      "preview": 0,
      "state": "finished",
      "timestart": 1775355988,
      "timefinish": 1775356652,
      "timemodified": 1775356652,
      "timemodifiedoffline": 0,
      "timecheckstate": null,
      "sumgrades": 29,
      "gradednotificationsenttime": null
    }
  ],
  "warnings": []
}
```

| Field | Type | Description |
|---|---|---|
| `state` | string | `"finished"`, `"inprogress"`, `"abandoned"`, `"overdue"` |
| `sumgrades` | float | Raw sum of grades (out of quiz `sumgrades`). Here: 29/30 |
| `timestart` | int | When attempt started (Unix timestamp) |
| `timefinish` | int | When attempt ended (Unix timestamp) |
| `attempt` | int | Attempt number (1-based) |

### `mod_quiz_get_user_best_grade`

Get the user's best grade for a quiz.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=mod_quiz_get_user_best_grade&quizid=71353&userid=11763&moodlewsrestformat=json
```

#### Response (200 OK)

```json
{
  "hasgrade": true,
  "grade": 9.66667,
  "warnings": []
}
```

| Field | Type | Description |
|---|---|---|
| `hasgrade` | bool | Whether user has a grade |
| `grade` | float | Best grade (scaled to quiz max grade) |

---

## 9. Calendar / Upcoming Events

### `core_calendar_get_calendar_upcoming_view`

Retrieve upcoming calendar events across all courses for the authenticated user.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=core_calendar_get_calendar_upcoming_view&moodlewsrestformat=json
```

No additional parameters required (uses the authenticated user's context).

#### Response (200 OK)

```json
{
  "events": [
    {
      "id": 384164,
      "name": "Learning Test 03 (In-Class) opens",
      "description": "<div>This quiz will test Advanced Network programming...</div>",
      "descriptionformat": 1,
      "component": "mod_quiz",
      "modulename": "quiz",
      "instance": 5782,
      "activityname": "Learning Test 03 (In-Class)",
      "activitystr": "Quiz",
      "eventtype": "open",
      "timestart": 1782130200,
      "timeduration": 0,
      "timesort": 1782130200,
      "timeusermidnight": 1782079200,
      "visible": 1,
      "overdue": false,
      "iscourseevent": false,
      "iscategoryevent": false,
      "isactionevent": true,
      "candelete": false,
      "canedit": false,
      "editurl": "",
      "viewurl": "https://courses.ut.edu.vn/calendar/view.php?id=384164",
      "purpose": "assessment",
      "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=623961",
      "formattedtime": "Hôm nay, 19:10",
      "formattedlocation": "",
      "normalisedeventtype": "open",
      "normalisedeventtypetext": "mở",
      "action": {
        "name": "opens",
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=623961",
        "itemcount": 1,
        "actionable": true,
        "showitemcount": false
      },
      "course": {
        "id": 21011,
        "fullname": "[012012301304] - Lập trình mạng - 7480201390613",
        "shortname": "[TT]_HKII2025-2026_Lập trình mạng_012012301304",
        "idnumber": "75012012301304",
        "summary": "",
        "summaryformat": 1,
        "startdate": 1771995603,
        "enddate": 1782363603,
        "visible": true,
        "showactivitydates": true,
        "showcompletionconditions": true,
        "pdfexportfont": "",
        "fullnamedisplay": "[012012301304] - Lập trình mạng - 7480201390613",
        "viewurl": "https://courses.ut.edu.vn/course/view.php?id=21011",
        "courseimage": "...",
        "progress": 3
      }
    },
    {
      "id": 391572,
      "name": "Điểm danh",
      "description": "",
      "component": "mod_attendance",
      "modulename": "attendance",
      "activityname": "Điểm danh",
      "eventtype": "attendance",
      "timestart": 1782440700,
      "timeduration": 9000,
      "visible": 1,
      "overdue": false,
      "isactionevent": false,
      "purpose": "administration",
      "formattedtime": "Friday, 26 June, 09:25 » 11:55",
      "course": {
        "id": 21252,
        "fullname": "[012012103107] - Lập trình Web - 7480102109360",
        "shortname": "[TT]_HKII2025-2026_Lập trình Web_012012103107"
      }
    }
  ],
  "defaulteventcontext": 5,
  "filter_selector": "...",
  "courseid": 0,
  "categoryid": 0,
  "isloggedin": true,
  "date": {
    "seconds": 0,
    "minutes": 0,
    "hours": 18,
    "mday": 22,
    "wday": 1,
    "mon": 6,
    "year": 2026,
    "yday": 172,
    "weekday": "Monday",
    "month": "June",
    "timestamp": 1782079200
  }
}
```

#### Event Field Reference

| Field | Type | Description |
|---|---|---|
| `id` | int | Event ID |
| `name` | string | Event title |
| `description` | string | HTML description |
| `component` | string | Source module: `mod_quiz`, `mod_assign`, `mod_attendance`, etc. |
| `modulename` | string | Module short name: `quiz`, `assign`, `attendance`, etc. |
| `activityname` | string | Name of the linked activity |
| `eventtype` | string | See table below |
| `timestart` | int | Event start time (Unix timestamp) |
| `timeduration` | int | Duration in **seconds** (`0` = instant event) |
| `visible` | int | `1` = visible |
| `overdue` | bool | Whether the event is past due |
| `isactionevent` | bool | Whether the event requires action (e.g., quiz attempt) |
| `purpose` | string | `assessment`, `administration`, `communication` |
| `url` | string | Direct URL to the activity |
| `formattedtime` | string | Localized time string |
| `course` | object | Course object with `id`, `fullname`, `shortname` |
| `action` | object\|null | Action details if `isactionevent=true` |

#### Event Types

| `eventtype` | Description |
|---|---|
| `open` | An activity just opened (quiz available, etc.) |
| `close` | An activity is about to close (submission deadline) |
| `due` | An assignment/activity is due |
| `attendance` | An attendance session |
| `user` | User-created event |
| `site` | Site-wide event |

> [!TIP]
> For more fine-grained event queries, use:
> - `core_calendar_get_action_events_by_course` — events for a specific course
> - `core_calendar_get_action_events_by_courses` — events across multiple courses
> - `core_calendar_get_action_events_by_timesort` — events within a time range
> - `core_calendar_get_calendar_day_view` — events for a specific day
> - `core_calendar_get_calendar_monthly_view` — events for a specific month

### `core_calendar_get_action_events_by_timesort`

Get action events (deadlines, opens, closes) within a time range across all courses. **This is the most useful endpoint for polling upcoming deadlines.**

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=core_calendar_get_action_events_by_timesort&timesortfrom={UNIX_NOW}&timesortto={UNIX_NOW+48h}&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `timesortfrom` | int | ✅ | Start of time range (Unix timestamp) |
| `timesortto` | int | ❌ | End of time range (Unix timestamp). Optional — defaults to far future |
| `limitnum` | int | ❌ | Max events to return (default: 20) |

#### Response (200 OK)

Same structure as `core_calendar_get_calendar_upcoming_view` → `events` array. Each event has:
- `id`, `name`, `component`, `modulename`, `eventtype`, `timestart`, `timeduration`
- `course` object with `id`, `fullname`
- `action` object with `name`, `url`, `actionable`
- `overdue` boolean

> [!IMPORTANT]
> This is the **recommended endpoint for deadline alerts**. Set `timesortfrom = now` and `timesortto = now + 48*3600` to get all deadlines in the next 48 hours.

### `core_course_get_updates_since`

Check what has changed in a course since a given timestamp. Useful for **polling-based change detection**.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=core_course_get_updates_since&courseid=21252&since=1781900000&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `courseid` | int | ✅ | Course ID |
| `since` | int | ✅ | Unix timestamp — only return changes after this time |

#### Response (200 OK)

```json
{
  "instances": [
    {
      "contextlevel": "module",
      "id": 664188,
      "updates": [
        {"name": "completion"},
        {"name": "gradeitems", "itemids": [269777]},
        {"name": "submissions", "itemids": [4792757]}
      ]
    }
  ],
  "warnings": [
    {
      "item": "module",
      "itemid": 617325,
      "warningcode": "missingcallback",
      "message": "This module does not implement the check_updates_since callback: module"
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `instances[].id` | int | Course Module ID (`cmid`) that changed |
| `instances[].updates[].name` | string | What changed: `completion`, `gradeitems`, `submissions`, `configuration` |
| `instances[].updates[].itemids` | array | Specific item IDs that changed |
| `warnings` | array | Modules that don't support update checking |

> [!TIP]
> Use this for **efficient polling**: store `last_check_timestamp`, then query `since=last_check_timestamp` to find only new changes without re-fetching all data.

---

## 10. Course Contents (Sections & Modules)

### `core_course_get_contents`

Retrieve the full structure of a course — sections (topics) and their modules (activities/resources).

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=core_course_get_contents&courseid=21252&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `courseid` | int | ✅ | Course ID |

#### Response (200 OK)

Example for **Lập trình Web** (`courseid=21252`) — 5 sections:

```json
[
  {
    "id": 212627,
    "name": "THÔNG TIN CHUNG",
    "visible": 1,
    "summary": "",
    "summaryformat": 1,
    "section": 0,
    "hiddenbynumsections": 0,
    "uservisible": true,
    "modules": [
      {
        "id": 617323,
        "url": "https://courses.ut.edu.vn/mod/page/view.php?id=617323",
        "name": "Thông tin học phần",
        "instance": 164524,
        "contextid": 779753,
        "visible": 1,
        "uservisible": true,
        "visibleoncoursepage": 1,
        "modicon": "https://courses.ut.edu.vn/theme/image.php/edly/page/1782075603/monologo",
        "modname": "page",
        "modplural": "Pages",
        "indent": 0,
        "onclick": "",
        "afterlink": null,
        "customdata": "",
        "noviewlink": false,
        "completion": 1,
        "completiondata": {
          "state": 0,
          "timecompleted": 0,
          "overrideby": null,
          "valueused": false,
          "hascompletion": true,
          "isautomatic": false,
          "istrackeduser": true,
          "uservisible": true,
          "details": []
        },
        "dates": []
      },
      {
        "id": 617324,
        "name": "Thông tin Giảng Viên",
        "modname": "page",
        "completion": 1
      }
    ]
  },
  {
    "id": 254653,
    "name": "HOẠT ĐỘNG LỚP HỌC",
    "visible": 1,
    "section": 1,
    "modules": [
      {
        "id": 617325,
        "name": "Điểm danh",
        "modname": "attendance",
        "url": "https://courses.ut.edu.vn/mod/attendance/view.php?id=617325"
      },
      {
        "id": 617387,
        "name": "Zalo lớp",
        "modname": "url",
        "url": "https://courses.ut.edu.vn/mod/url/view.php?id=617387"
      },
      {
        "id": 663216,
        "name": "Link Google Meet",
        "modname": "googlemeet",
        "url": "https://courses.ut.edu.vn/mod/googlemeet/view.php?id=663216"
      }
    ]
  },
  {
    "id": 254654,
    "name": "TÀI LIỆU MÔN HỌC",
    "visible": 1,
    "section": 2,
    "modules": [
      {
        "id": 617326,
        "name": "Đề cương học phần",
        "modname": "resource",
        "url": "https://courses.ut.edu.vn/mod/resource/view.php?id=617326",
        "contents": [
          {
            "type": "file",
            "filename": "Decuong_LaptrinhWeb.pdf",
            "filepath": "/",
            "filesize": 158236,
            "fileurl": "https://courses.ut.edu.vn/webservice/pluginfile.php/779756/mod_resource/content/1/Decuong_LaptrinhWeb.pdf?forcedownload=1",
            "timecreated": 1771999223,
            "timemodified": 1771999223,
            "sortorder": 1,
            "userid": 4102,
            "author": null,
            "license": "unknown"
          }
        ]
      },
      {
        "id": 617327,
        "name": "Chương 1. Giới thiệu tổng quan về web",
        "modname": "resource"
      },
      {
        "id": 617328,
        "name": "Chương 2. Ngôn ngữ định kiểu CSS",
        "modname": "resource"
      }
    ]
  },
  {
    "id": 254655,
    "name": "NỘP BÀI E-LEARING/THỰC HÀNH",
    "visible": 1,
    "section": 3,
    "modules": [
      {
        "id": 656402,
        "name": "Bài tập cá nhân phần HTML + CSS",
        "modname": "assign",
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=656402",
        "completion": 1,
        "completiondata": {
          "state": 1,
          "timecompleted": 1781454664,
          "hascompletion": true,
          "isautomatic": false,
          "istrackeduser": true,
          "uservisible": true
        }
      },
      {
        "id": 664188,
        "name": "Bài tập cá nhân phần JavaScript",
        "modname": "assign"
      }
    ]
  },
  {
    "id": 254656,
    "name": "BÀI TẬP LỚN THEO NHÓM",
    "visible": 1,
    "section": 4,
    "modules": [
      {
        "id": 617364,
        "name": "Đăng ký nhóm cho bài cuối kỳ",
        "modname": "groupselect"
      },
      {
        "id": 640234,
        "name": "Nộp bài nhóm cuối kỳ",
        "modname": "assign"
      }
    ]
  }
]
```

#### Section Field Reference

| Field | Type | Description |
|---|---|---|
| `id` | int | Section ID |
| `name` | string | Section title (topic name) |
| `visible` | int | `1` = visible, `0` = hidden |
| `section` | int | Section number (0 = general/top) |
| `summary` | string | Section summary (HTML) |
| `modules` | array | Array of module (activity/resource) objects |

#### Module Field Reference

| Field | Type | Description |
|---|---|---|
| `id` | int | Course Module ID (`cmid`) — used in many APIs |
| `name` | string | Activity/resource name |
| `modname` | string | Module type — see table below |
| `url` | string | Direct URL to view the module |
| `visible` | int | `1` = visible |
| `uservisible` | bool | Whether visible to the current user |
| `completion` | int | Completion tracking mode: `0`=none, `1`=manual, `2`=automatic |
| `completiondata` | object | Completion status (see below) |
| `contents` | array | File contents (for `resource` modules) |
| `dates` | array | Important dates for the activity |

#### Module Types (`modname` values)

| `modname` | Description |
|---|---|
| `page` | Web page content |
| `resource` | File download (PDF, DOCX, etc.) |
| `assign` | Assignment (submission required) |
| `quiz` | Quiz/test |
| `attendance` | Attendance tracking |
| `forum` | Discussion forum |
| `url` | External URL link |
| `googlemeet` | Google Meet link |
| `groupselect` | Group self-selection |
| `label` | Text label (display only, no link) |
| `folder` | File folder |
| `h5pactivity` | H5P interactive content |
| `feedback` | Feedback/survey |
| `choice` | Choice/poll activity |
| `glossary` | Glossary |
| `wiki` | Wiki |
| `chat` | Chat room |

#### Completion Data

| Field | Type | Description |
|---|---|---|
| `state` | int | `0`=incomplete, `1`=complete, `2`=complete_pass, `3`=complete_fail |
| `timecompleted` | int | When completed (Unix timestamp). `0` = not completed |
| `hascompletion` | bool | Whether completion tracking is set up |
| `isautomatic` | bool | `true` = automatic tracking, `false` = manual checkbox |

---

## 11. Activity Completion Status

### `core_completion_get_activities_completion_status`

Get the completion status of all activities in a course for a specific user.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=core_completion_get_activities_completion_status&courseid=21129&userid=11763&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `courseid` | int | ✅ | Course ID |
| `userid` | int | ✅ | User ID |

#### Response (200 OK)

Example for **An toàn thông tin** (`courseid=21129`) — 33 activities:

```json
{
  "statuses": [
    {
      "cmid": 551305,
      "modname": "label",
      "instance": 17849,
      "state": 0,
      "timecompleted": 0,
      "tracking": 1,
      "overrideby": null,
      "valueused": false,
      "hascompletion": true,
      "isautomatic": false,
      "istrackeduser": true,
      "uservisible": true
    },
    {
      "cmid": 551307,
      "modname": "resource",
      "instance": 164818,
      "state": 1,
      "timecompleted": 1776347023,
      "tracking": 1,
      "overrideby": null,
      "valueused": false,
      "hascompletion": true,
      "isautomatic": false,
      "istrackeduser": true,
      "uservisible": true
    },
    {
      "cmid": 551308,
      "modname": "attendance",
      "instance": 7626,
      "state": 0,
      "timecompleted": 0,
      "tracking": 1,
      "overrideby": null,
      "valueused": false,
      "hascompletion": true,
      "isautomatic": false,
      "istrackeduser": true,
      "uservisible": true
    }
  ]
}
```

#### Status Field Reference

| Field | Type | Description |
|---|---|---|
| `cmid` | int | Course Module ID |
| `modname` | string | Module type (`label`, `resource`, `quiz`, `assign`, `attendance`, etc.) |
| `instance` | int | Module instance ID |
| `state` | int | **Completion state** — see table below |
| `timecompleted` | int | When completed (Unix timestamp). `0` = not completed |
| `tracking` | int | Tracking type: `0`=none, `1`=manual, `2`=automatic |
| `overrideby` | int\|null | User ID who overrode the completion (null = no override) |
| `hascompletion` | bool | Whether the activity has completion tracking |
| `isautomatic` | bool | Whether completion is automatic (condition-based) |
| `istrackeduser` | bool | Whether the user is being tracked for completion |
| `uservisible` | bool | Whether the activity is visible to the user |

#### Completion States

| State | Meaning |
|---|---|
| `0` | Incomplete |
| `1` | Complete |
| `2` | Complete — Pass |
| `3` | Complete — Fail |

---

## 12. Notifications

### `message_popup_get_popup_notifications`

Get popup notifications for the user.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=message_popup_get_popup_notifications&useridto=11763&limit=5&moodlewsrestformat=json
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `useridto` | int | ✅ | Recipient user ID |
| `limit` | int | ❌ | Max notifications to return (default: 20) |
| `offset` | int | ❌ | Pagination offset (default: 0) |
| `newestfirst` | bool | ❌ | Sort by newest first (default: true) |

#### Response (200 OK)

```json
{
  "notifications": [],
  "unreadcount": 0
}
```

| Field | Type | Description |
|---|---|---|
| `notifications` | array | Array of notification objects |
| `unreadcount` | int | Number of unread notifications |

Each notification object (when present) contains:

| Field | Type | Description |
|---|---|---|
| `id` | int | Notification ID |
| `useridfrom` | int | Sender user ID |
| `useridto` | int | Recipient user ID |
| `subject` | string | Notification subject |
| `shortenedsubject` | string | Truncated subject |
| `text` | string | Full notification text (HTML) |
| `fullmessage` | string | Full message text |
| `fullmessageformat` | int | Message format |
| `fullmessagehtml` | string | HTML version of full message |
| `smallmessage` | string | Short version of message |
| `contexturl` | string | URL to the related context |
| `contexturlname` | string | Display name for the context URL |
| `timecreated` | int | Creation timestamp |
| `timecreatedpretty` | string | Formatted time string |
| `timeread` | int\|null | When read (null = unread) |
| `read` | bool | Whether notification was read |
| `deleted` | bool | Whether notification was deleted |
| `component` | string | Source component |
| `eventtype` | string | Event type |

### `message_popup_get_unread_popup_notification_count`

Get just the count of unread notifications.

#### Request

```
GET https://courses.ut.edu.vn/webservice/rest/server.php?wstoken=YOUR_TOKEN_HERE&wsfunction=message_popup_get_unread_popup_notification_count&useridto=11763&moodlewsrestformat=json
```

#### Response (200 OK)

```json
0
```

> [!NOTE]
> This returns a **raw integer**, not a JSON object. Parse accordingly.

---

## 12b. Known Limitations & Disabled Endpoints

The following endpoints were tested but **do not work** for student accounts. AI agents should **not** call these.

### 🔒 Permission Denied (Student Role)

| Function | Error | Reason |
|---|---|---|
| `core_enrol_get_enrolled_users` | `nopermissions` | Students cannot view participant list |
| `mod_assign_get_grades` | `nopermissions` | Students cannot access grade management |
| `mod_assign_get_participant` | `nopermission` | Teacher-only endpoint |
| `mod_assign_list_participants` | `nopermission` | Teacher-only endpoint |
| `core_notes_get_course_notes` | `nopermissions` | Students cannot view notes |
| `core_group_get_course_groups` | `nopermissions` | Students cannot manage groups (use `core_group_get_course_user_groups` instead) |

### ⚠️ Feature Disabled on UTH Server

| Function | Error Code | Reason |
|---|---|---|
| `core_badges_get_user_badges` | `badgesdisabled` | Badges not enabled on this site |
| `core_calendar_get_calendar_export_token` | disabled | Calendar export is disabled |
| `core_competency_list_course_competencies` | disabled | Competencies not enabled |
| `tool_mobile_get_autologin_key` | `apprequired` | Only via official Moodle mobile app |

### ⚠️ Access Exception (Not in Service)

| Function | Error | Note |
|---|---|---|
| `mod_attendance_get_courses_with_today_sessions` | `accessexception` | Not in `moodle_mobile_app` service |
| `mod_attendance_get_sessions` | `accessexception` | Not in `moodle_mobile_app` service |
| `core_courseformat_get_state` | `accessexception` | Not in `moodle_mobile_app` service |

> [!WARNING]
> **Attendance data** is NOT directly accessible via the dedicated `mod_attendance` API for student accounts. However, attendance **events** appear in calendar APIs (`core_calendar_get_calendar_upcoming_view`) and attendance **grades** appear in grade APIs (`gradereport_user_get_grade_items` with `itemmodule="attendance"`).

---

## 13. Full Function List (All 421)

Below is the complete list of all 421 web service functions available via the `moodle_mobile_app` service, grouped by category.

### Core — Course (14)

| Function | Description |
|---|---|
| `core_course_check_updates` | Check if there are updates for the course modules |
| `core_course_get_categories` | Get course categories |
| `core_course_get_contents` | Get course contents (sections & modules) |
| `core_course_get_course_module` | Get a single course module by cmid |
| `core_course_get_course_module_by_instance` | Get a course module by module instance |
| `core_course_get_courses` | Get course details by course IDs |
| `core_course_get_courses_by_field` | Get courses by field value |
| `core_course_get_enrolled_courses_by_timeline_classification` | Get enrolled courses sorted by timeline |
| `core_course_get_enrolled_courses_with_action_events_by_timeline_classification` | Get enrolled courses with action events |
| `core_course_get_recent_courses` | Get recently accessed courses |
| `core_course_get_updates_since` | Check updates since a given timestamp |
| `core_course_get_user_administration_options` | Get admin options for user in courses |
| `core_course_get_user_navigation_options` | Get navigation options for user in courses |
| `core_course_search_courses` | Search for courses |

### Core — Calendar (14)

| Function | Description |
|---|---|
| `core_calendar_create_calendar_events` | Create calendar events |
| `core_calendar_delete_calendar_events` | Delete calendar events |
| `core_calendar_get_action_events_by_course` | Get action events for a single course |
| `core_calendar_get_action_events_by_courses` | Get action events for multiple courses |
| `core_calendar_get_action_events_by_timesort` | Get action events sorted by time |
| `core_calendar_get_allowed_event_types` | Get allowed event types |
| `core_calendar_get_calendar_access_information` | Get calendar access info |
| `core_calendar_get_calendar_day_view` | Get events for a specific day |
| `core_calendar_get_calendar_event_by_id` | Get a single event by ID |
| `core_calendar_get_calendar_events` | Get calendar events (legacy) |
| `core_calendar_get_calendar_monthly_view` | Get events for a specific month |
| `core_calendar_get_calendar_upcoming_view` | Get upcoming events |
| `core_calendar_submit_create_update_form` | Submit event creation/update form |
| `core_calendar_update_event_start_day` | Update event start day |

### Core — Grades (13)

| Function | Description |
|---|---|
| `core_grades_get_enrolled_users_for_search_widget` | Get enrolled users for grade search |
| `core_grades_get_enrolled_users_for_selector` | Get enrolled users for selector |
| `core_grades_get_feedback` | Get grade feedback |
| `core_grades_get_gradeitems` | Get grade items |
| `core_grades_get_groups_for_search_widget` | Get groups for search widget |
| `core_grades_get_groups_for_selector` | Get groups for selector |
| `gradereport_grader_get_users_in_report` | Get users in grader report |
| `gradereport_overview_get_course_grades` | Get course grades overview |
| `gradereport_singleview_get_grade_items_for_search_widget` | Get grade items for search |
| `gradereport_user_get_access_information` | Get grade report access info |
| `gradereport_user_get_grade_items` | Get detailed grade items per course |
| `gradereport_user_get_grades_table` | Get the full grades table HTML |

### Core — Messages (30+)

| Function | Description |
|---|---|
| `core_message_block_user` | Block a user |
| `core_message_confirm_contact_request` | Confirm contact request |
| `core_message_create_contact_request` | Send contact request |
| `core_message_data_for_messagearea_search_messages` | Search messages |
| `core_message_decline_contact_request` | Decline contact request |
| `core_message_delete_contacts` | Delete contacts |
| `core_message_delete_conversations_by_id` | Delete conversations |
| `core_message_delete_message` | Delete a message |
| `core_message_delete_message_for_all_users` | Delete message for all |
| `core_message_get_blocked_users` | Get blocked users |
| `core_message_get_contact_requests` | Get contact requests |
| `core_message_get_conversation` | Get a conversation |
| `core_message_get_conversation_between_users` | Get conversation between users |
| `core_message_get_conversation_counts` | Get conversation counts |
| `core_message_get_conversation_members` | Get conversation members |
| `core_message_get_conversations` | Get all conversations |
| `core_message_get_member_info` | Get member info |
| `core_message_get_message_processor` | Get message processor |
| `core_message_get_messages` | Get messages |
| `core_message_get_received_contact_requests_count` | Get received request count |
| `core_message_get_self_conversation` | Get self conversation |
| `core_message_get_unread_conversation_counts` | Get unread counts |
| `core_message_get_unread_conversations_count` | Get unread conversation count |
| `core_message_get_user_contacts` | Get user contacts |
| `core_message_get_user_message_preferences` | Get message preferences |
| `core_message_get_user_notification_preferences` | Get notification preferences |
| `core_message_mark_all_conversation_messages_as_read` | Mark conversation as read |
| `core_message_mark_all_notifications_as_read` | Mark all notifications as read |
| `core_message_mark_message_read` | Mark message as read |
| `core_message_mark_notification_read` | Mark notification as read |
| `core_message_mute_conversations` | Mute conversations |
| `core_message_search_contacts` | Search contacts |
| `core_message_send_instant_messages` | Send instant messages |
| `core_message_send_messages_to_conversation` | Send to conversation |
| `core_message_set_favourite_conversations` | Set favourite conversations |
| `core_message_unblock_user` | Unblock a user |
| `core_message_unmute_conversations` | Unmute conversations |
| `core_message_unset_favourite_conversations` | Unset favourite conversations |
| `message_popup_get_popup_notifications` | Get popup notifications |
| `message_popup_get_unread_popup_notification_count` | Get unread notification count |

### Mod — Assign (18)

| Function | Description |
|---|---|
| `mod_assign_copy_previous_attempt` | Copy previous attempt |
| `mod_assign_get_assignments` | Get assignments for courses |
| `mod_assign_get_grades` | Get assignment grades |
| `mod_assign_get_participant` | Get participant info |
| `mod_assign_get_submission_status` | Get submission status for a user |
| `mod_assign_get_user_flags` | Get user flags |
| `mod_assign_get_user_mappings` | Get user mappings |
| `mod_assign_list_participants` | List participants |
| `mod_assign_lock_submissions` | Lock submissions |
| `mod_assign_reveal_identities` | Reveal identities (blind marking) |
| `mod_assign_revert_submissions_to_draft` | Revert to draft |
| `mod_assign_save_grade` | Save a grade |
| `mod_assign_save_grades` | Save multiple grades |
| `mod_assign_save_submission` | Save a submission |
| `mod_assign_save_user_extensions` | Save user extensions |
| `mod_assign_set_user_flags` | Set user flags |
| `mod_assign_submit_for_grading` | Submit for grading |
| `mod_assign_submit_grading_form` | Submit grading form |
| `mod_assign_unlock_submissions` | Unlock submissions |
| `mod_assign_view_assign` | Trigger view event |
| `mod_assign_view_grading_table` | Trigger grading table view |
| `mod_assign_view_submission_status` | Trigger submission status view |

### Mod — Quiz (16)

| Function | Description |
|---|---|
| `mod_quiz_get_attempt_access_information` | Get attempt access info |
| `mod_quiz_get_attempt_data` | Get attempt data |
| `mod_quiz_get_attempt_review` | Get attempt review |
| `mod_quiz_get_attempt_summary` | Get attempt summary |
| `mod_quiz_get_combined_review_options` | Get combined review options |
| `mod_quiz_get_quizzes_by_courses` | Get quizzes by courses |
| `mod_quiz_get_quiz_access_information` | Get quiz access info |
| `mod_quiz_get_quiz_feedback_for_grade` | Get feedback for grade |
| `mod_quiz_get_quiz_required_qtypes` | Get required question types |
| `mod_quiz_get_user_attempts` | Get user attempts |
| `mod_quiz_get_user_best_grade` | Get user best grade |
| `mod_quiz_process_attempt` | Process/submit attempt |
| `mod_quiz_save_attempt` | Save attempt in progress |
| `mod_quiz_start_attempt` | Start a new attempt |
| `mod_quiz_view_attempt` | Trigger view attempt event |
| `mod_quiz_view_attempt_review` | Trigger attempt review event |
| `mod_quiz_view_attempt_summary` | Trigger attempt summary event |
| `mod_quiz_view_quiz` | Trigger quiz view event |

### Mod — Forum (16)

| Function | Description |
|---|---|
| `mod_forum_add_discussion` | Add new discussion |
| `mod_forum_add_discussion_post` | Add discussion post |
| `mod_forum_can_add_discussion` | Check if user can add discussion |
| `mod_forum_delete_post` | Delete a post |
| `mod_forum_get_discussion_posts` | Get posts in a discussion |
| `mod_forum_get_discussion_posts_by_userid` | Get posts by user |
| `mod_forum_get_forum_access_information` | Get forum access info |
| `mod_forum_get_forum_discussions` | Get forum discussions |
| `mod_forum_get_forums_by_courses` | Get forums by courses |
| `mod_forum_prepare_draft_area_for_post` | Prepare draft area |
| `mod_forum_set_lock_state` | Lock/unlock discussion |
| `mod_forum_set_pin_state` | Pin/unpin discussion |
| `mod_forum_set_subscription_state` | Set subscription state |
| `mod_forum_toggle_favourite_state` | Toggle favourite |
| `mod_forum_update_discussion_post` | Update post |
| `mod_forum_view_forum` | Trigger view event |
| `mod_forum_view_forum_discussion` | Trigger discussion view |

### Core — User (12)

| Function | Description |
|---|---|
| `core_user_add_user_device` | Register mobile device |
| `core_user_add_user_private_files` | Add private files |
| `core_user_agree_site_policy` | Agree to site policy |
| `core_user_get_course_user_profiles` | Get user profile in course context |
| `core_user_get_private_files_info` | Get private files info |
| `core_user_get_user_preferences` | Get user preferences |
| `core_user_get_users_by_field` | Get users by field value |
| `core_user_remove_user_device` | Remove mobile device |
| `core_user_set_user_preferences` | Set user preferences |
| `core_user_update_picture` | Update profile picture |
| `core_user_update_user_preferences` | Update user preferences |
| `core_user_view_user_list` | Trigger user list view |
| `core_user_view_user_profile` | Trigger profile view |

### Core — Completion (4)

| Function | Description |
|---|---|
| `core_completion_get_activities_completion_status` | Get completion status for all activities |
| `core_completion_get_course_completion_status` | Get overall course completion status |
| `core_completion_mark_course_self_completed` | Mark course as self-completed |
| `core_completion_update_activity_completion_status_manually` | Manually toggle activity completion |

### Core — Enrol (7)

| Function | Description |
|---|---|
| `core_enrol_get_course_enrolment_methods` | Get enrolment methods |
| `core_enrol_get_enrolled_users` | Get enrolled users in a course |
| `core_enrol_get_enrolled_users_with_capability` | Get enrolled users with capability |
| `core_enrol_get_users_courses` | Get courses a user is enrolled in |
| `core_enrol_search_users` | Search users for enrolment |
| `enrol_guest_get_instance_info` | Get guest enrolment info |
| `enrol_self_enrol_user` | Self-enrol in a course |

### Core — Webservice (2)

| Function | Description |
|---|---|
| `core_webservice_get_site_info` | Get site info and user details |
| `core_get_string` | Get a language string |
| `core_get_strings` | Get multiple language strings |
| `core_get_component_strings` | Get all strings for a component |
| `core_get_user_dates` | Get user-formatted dates |

### Tool — Mobile (8)

| Function | Description |
|---|---|
| `tool_mobile_call_external_functions` | Call multiple WS functions in one request |
| `tool_mobile_get_autologin_key` | Get auto-login key |
| `tool_mobile_get_config` | Get mobile app configuration |
| `tool_mobile_get_content` | Get content for mobile |
| `tool_mobile_get_plugins_supporting_mobile` | Get mobile-enabled plugins |
| `tool_mobile_get_public_config` | Get public config (no auth needed) |
| `tool_mobile_get_tokens_for_qr_login` | Get tokens for QR login |
| `tool_mobile_validate_subscription_key` | Validate subscription key |

### Mod — Other Modules

| Function | Description |
|---|---|
| `mod_attendance_get_courses_with_today_sessions` | Get courses with attendance today |
| `mod_attendance_get_session` | Get attendance session details |
| `mod_attendance_get_sessions` | Get attendance sessions |
| `mod_book_get_books_by_courses` | Get books by courses |
| `mod_book_view_book` | Trigger book view event |
| `mod_chat_get_chat_latest_messages` | Get latest chat messages |
| `mod_chat_get_chat_users` | Get chat users |
| `mod_chat_get_chats_by_courses` | Get chats by courses |
| `mod_chat_login_user` | Login to chat |
| `mod_chat_send_chat_message` | Send chat message |
| `mod_chat_view_chat` | Trigger chat view |
| `mod_choice_delete_choice_responses` | Delete choice responses |
| `mod_choice_get_choice_options` | Get choice options |
| `mod_choice_get_choice_results` | Get choice results |
| `mod_choice_get_choices_by_courses` | Get choices by courses |
| `mod_choice_submit_choice_response` | Submit choice response |
| `mod_choice_view_choice` | Trigger choice view |
| `mod_data_add_entry` | Add database entry |
| `mod_data_approve_entry` | Approve database entry |
| `mod_data_delete_entry` | Delete database entry |
| `mod_data_delete_saved_preset` | Delete saved preset |
| `mod_data_get_data_access_information` | Get access info |
| `mod_data_get_databases_by_courses` | Get databases by courses |
| `mod_data_get_entries` | Get database entries |
| `mod_data_get_entry` | Get a single entry |
| `mod_data_get_fields` | Get database fields |
| `mod_data_get_mapping_information` | Get mapping info |
| `mod_data_search_entries` | Search database entries |
| `mod_data_update_entry` | Update database entry |
| `mod_data_view_database` | Trigger view event |
| `mod_feedback_get_analysis` | Get feedback analysis |
| `mod_feedback_get_current_completed_tmp` | Get temp completed |
| `mod_feedback_get_feedback_access_information` | Get access info |
| `mod_feedback_get_feedbacks_by_courses` | Get feedbacks by courses |
| `mod_feedback_get_finished_responses` | Get finished responses |
| `mod_feedback_get_items` | Get feedback items |
| `mod_feedback_get_last_completed` | Get last completed |
| `mod_feedback_get_non_respondents` | Get non-respondents |
| `mod_feedback_get_page_items` | Get items for a page |
| `mod_feedback_get_responses_analysis` | Get responses analysis |
| `mod_feedback_get_unfinished_responses` | Get unfinished responses |
| `mod_feedback_launch_feedback` | Launch feedback |
| `mod_feedback_process_page` | Process a page |
| `mod_feedback_view_feedback` | Trigger view event |
| `mod_folder_view_folder` | Trigger folder view |
| `mod_glossary_add_entry` | Add glossary entry |
| `mod_glossary_delete_entry` | Delete glossary entry |
| `mod_glossary_get_authors` | Get glossary authors |
| `mod_glossary_get_categories` | Get glossary categories |
| `mod_glossary_get_entries_by_author` | Get entries by author |
| `mod_glossary_get_entries_by_author_id` | Get entries by author ID |
| `mod_glossary_get_entries_by_category` | Get entries by category |
| `mod_glossary_get_entries_by_date` | Get entries by date |
| `mod_glossary_get_entries_by_letter` | Get entries by letter |
| `mod_glossary_get_entries_by_search` | Search entries |
| `mod_glossary_get_entries_by_term` | Get entries by term |
| `mod_glossary_get_entries_to_approve` | Get entries to approve |
| `mod_glossary_get_entry_by_id` | Get entry by ID |
| `mod_glossary_get_glossaries_by_courses` | Get glossaries by courses |
| `mod_glossary_prepare_entry_for_edition` | Prepare entry for edit |
| `mod_glossary_update_entry` | Update glossary entry |
| `mod_glossary_view_entry` | Trigger entry view |
| `mod_glossary_view_glossary` | Trigger glossary view |
| `mod_h5pactivity_get_attempts` | Get H5P attempts |
| `mod_h5pactivity_get_h5pactivities_by_courses` | Get H5P activities |
| `mod_h5pactivity_get_h5pactivity_access_information` | Get access info |
| `mod_h5pactivity_get_results` | Get attempt results |
| `mod_h5pactivity_get_user_attempts` | Get user attempts |
| `mod_h5pactivity_log_report_viewed` | Log report viewed |
| `mod_h5pactivity_view_h5pactivity` | Trigger view event |
| `mod_imscp_get_imscps_by_courses` | Get IMSCP by courses |
| `mod_imscp_view_imscp` | Trigger view event |
| `mod_label_get_labels_by_courses` | Get labels by courses |
| `mod_lesson_finish_attempt` | Finish lesson attempt |
| `mod_lesson_get_attempts_overview` | Get attempts overview |
| `mod_lesson_get_content_pages_viewed` | Get content pages viewed |
| `mod_lesson_get_lesson` | Get lesson details |
| `mod_lesson_get_lesson_access_information` | Get access info |
| `mod_lesson_get_lessons_by_courses` | Get lessons by courses |
| `mod_lesson_get_page_data` | Get page data |
| `mod_lesson_get_pages` | Get lesson pages |
| `mod_lesson_get_pages_possible_jumps` | Get possible jumps |
| `mod_lesson_get_questions_attempts` | Get question attempts |
| `mod_lesson_get_user_attempt` | Get user attempt |
| `mod_lesson_get_user_attempt_grade` | Get attempt grade |
| `mod_lesson_get_user_grade` | Get user grade |
| `mod_lesson_get_user_timers` | Get user timers |
| `mod_lesson_launch_attempt` | Launch attempt |
| `mod_lesson_process_page` | Process a page |
| `mod_lesson_view_lesson` | Trigger view event |
| `mod_lti_get_ltis_by_courses` | Get LTIs by courses |
| `mod_lti_get_tool_launch_data` | Get LTI launch data |
| `mod_lti_view_lti` | Trigger view event |
| `mod_page_get_pages_by_courses` | Get pages by courses |
| `mod_page_view_page` | Trigger page view |
| `mod_resource_get_resources_by_courses` | Get resources by courses |
| `mod_resource_view_resource` | Trigger resource view |
| `mod_scorm_get_scorm_access_information` | Get SCORM access info |
| `mod_scorm_get_scorm_attempt_count` | Get attempt count |
| `mod_scorm_get_scorm_sco_tracks` | Get SCO tracks |
| `mod_scorm_get_scorm_scoes` | Get SCORM SCOs |
| `mod_scorm_get_scorm_user_data` | Get user data |
| `mod_scorm_get_scorms_by_courses` | Get SCORMs by courses |
| `mod_scorm_insert_scorm_tracks` | Insert SCO tracks |
| `mod_scorm_launch_sco` | Launch SCO |
| `mod_scorm_view_scorm` | Trigger view event |
| `mod_survey_get_questions` | Get survey questions |
| `mod_survey_get_surveys_by_courses` | Get surveys by courses |
| `mod_survey_submit_answers` | Submit survey answers |
| `mod_survey_view_survey` | Trigger view event |
| `mod_url_get_urls_by_courses` | Get URLs by courses |
| `mod_url_view_url` | Trigger URL view |
| `mod_wiki_edit_page` | Edit wiki page |
| `mod_wiki_get_page_contents` | Get page contents |
| `mod_wiki_get_page_for_editing` | Get page for editing |
| `mod_wiki_get_subwiki_files` | Get subwiki files |
| `mod_wiki_get_subwiki_pages` | Get subwiki pages |
| `mod_wiki_get_subwikis` | Get subwikis |
| `mod_wiki_get_wikis_by_courses` | Get wikis by courses |
| `mod_wiki_new_page` | Create new wiki page |
| `mod_wiki_view_page` | Trigger page view |
| `mod_wiki_view_wiki` | Trigger wiki view |
| `mod_workshop_get_assessment` | Get workshop assessment |
| `mod_workshop_get_assessment_form_definition` | Get form definition |
| `mod_workshop_get_grades` | Get workshop grades |
| `mod_workshop_get_grades_report` | Get grades report |
| `mod_workshop_get_reviewer_assessments` | Get reviewer assessments |
| `mod_workshop_get_submission` | Get workshop submission |
| `mod_workshop_get_submission_assessments` | Get submission assessments |
| `mod_workshop_get_submissions` | Get workshop submissions |
| `mod_workshop_get_user_plan` | Get user plan |
| `mod_workshop_get_workshop_access_information` | Get access info |
| `mod_workshop_get_workshops_by_courses` | Get workshops by courses |
| `mod_workshop_view_submission` | Trigger submission view |
| `mod_workshop_view_workshop` | Trigger workshop view |

### Core — Other

| Function | Description |
|---|---|
| `core_auth_confirm_user` | Confirm user account |
| `core_auth_is_age_digital_consent_verification_enabled` | Check digital consent |
| `core_auth_is_minor` | Check if user is a minor |
| `core_auth_request_password_reset` | Request password reset |
| `core_auth_resend_confirmation_email` | Resend confirmation |
| `core_badges_get_user_badge_by_hash` | Get badge by hash |
| `core_badges_get_user_badges` | Get user badges |
| `core_block_get_course_blocks` | Get course blocks |
| `core_block_get_dashboard_blocks` | Get dashboard blocks |
| `core_blog_get_entries` | Get blog entries |
| `core_blog_view_entries` | Trigger blog view |
| `core_cohort_search_cohorts` | Search cohorts |
| `core_comment_add_comments` | Add comments |
| `core_comment_delete_comments` | Delete comments |
| `core_comment_get_comments` | Get comments |
| `core_competency_competency_viewed` | Log competency viewed |
| `core_competency_delete_evidence` | Delete competency evidence |
| `core_competency_get_scale_values` | Get scale values |
| `core_competency_grade_competency_in_course` | Grade competency |
| `core_competency_list_course_competencies` | List course competencies |
| `core_competency_user_competency_plan_viewed` | Log plan viewed |
| `core_competency_user_competency_viewed` | Log competency viewed |
| `core_competency_user_competency_viewed_in_course` | Log viewed in course |
| `core_competency_user_competency_viewed_in_plan` | Log viewed in plan |
| `core_contentbank_rename_content` | Rename content |
| `core_course_view_course` | Trigger course view event |
| `core_customfield_create_category` | Create custom field category |
| `core_customfield_delete_category` | Delete custom field category |
| `core_customfield_delete_field` | Delete custom field |
| `core_customfield_move_category` | Move custom field category |
| `core_customfield_move_field` | Move custom field |
| `core_customfield_reload_template` | Reload custom field template |
| `core_dynamic_tabs_get_content` | Get dynamic tab content |
| `core_fetch_notifications` | Fetch system notifications |
| `core_files_delete_draft_files` | Delete draft files |
| `core_files_get_files` | Get files |
| `core_files_upload` | Upload a file |
| `core_filters_get_available_in_context` | Get available filters |
| `core_form_get_filetypes_browser_data` | Get file type browser data |
| `core_group_get_activity_allowed_groups` | Get allowed groups |
| `core_group_get_activity_groupmode` | Get group mode |
| `core_group_get_course_groupings` | Get course groupings |
| `core_group_get_course_groups` | Get course groups |
| `core_group_get_course_user_groups` | Get user's groups in course |
| `core_h5p_get_trusted_h5p_file` | Get trusted H5P file |
| `core_notes_create_notes` | Create notes |
| `core_notes_delete_notes` | Delete notes |
| `core_notes_get_course_notes` | Get course notes |
| `core_notes_view_notes` | Trigger notes view |
| `core_output_load_fontawesome_icon_system_map` | Load FontAwesome map |
| `core_output_load_template` | Load Mustache template |
| `core_output_load_template_with_dependencies` | Load template with deps |
| `core_question_get_random_question_summaries` | Get random question summaries |
| `core_question_submit_tags_form` | Submit question tags |
| `core_question_update_flag` | Update question flag |
| `core_rating_add_rating` | Add a rating |
| `core_rating_get_item_ratings` | Get item ratings |
| `core_reportbuilder_list_reports` | List reports |
| `core_reportbuilder_retrieve_report` | Retrieve a report |
| `core_reportbuilder_view_report` | View a report |
| `core_search_get_relevant_users` | Search relevant users |
| `core_search_get_results` | Get search results |
| `core_search_get_search_areas_list` | Get search areas |
| `core_search_get_top_results` | Get top search results |
| `core_search_view_results` | Trigger search results view |
| `core_session_time_remaining` | Get session time remaining |
| `core_session_touch` | Touch session (keep alive) |
| `core_table_get_dynamic_table_content` | Get dynamic table content |
| `core_tag_get_tag_areas` | Get tag areas |
| `core_tag_get_tag_cloud` | Get tag cloud |
| `core_tag_get_tag_collections` | Get tag collections |
| `core_tag_get_tagindex` | Get tag index |
| `core_tag_get_tagindex_per_area` | Get tag index per area |
| `core_tag_update_tags` | Update tags |
| `core_update_inplace_editable` | Update inplace editable |
| `core_user_search_identity` | Search user identity |
| `core_xapi_delete_states` | Delete xAPI states |
| `core_xapi_delete_state` | Delete xAPI state |
| `core_xapi_get_state` | Get xAPI state |
| `core_xapi_get_states` | Get xAPI states |
| `core_xapi_post_state` | Post xAPI state |
| `core_xapi_statement_post` | Post xAPI statement |

### Report & Tool

| Function | Description |
|---|---|
| `report_competency_data_for_report` | Get competency report data |
| `report_insights_action_executed` | Report insights action |
| `report_insights_set_fixed_prediction` | Set fixed prediction |
| `report_insights_set_notuseful_prediction` | Set not-useful prediction |
| `tool_analytics_potential_contexts` | Get potential analytics contexts |
| `tool_lp_data_for_competencies_manage_page` | Get competencies manage data |
| `tool_lp_data_for_course_competencies_page` | Get course competencies data |
| `tool_lp_data_for_plan_page` | Get plan page data |
| `tool_lp_data_for_plans_page` | Get plans page data |
| `tool_lp_data_for_related_competencies_section` | Get related competencies |
| `tool_lp_data_for_template_competencies_page` | Get template competencies |
| `tool_lp_data_for_templates_manage_page` | Get templates manage data |
| `tool_lp_data_for_user_competency_summary` | Get competency summary |
| `tool_lp_data_for_user_competency_summary_in_course` | Get summary in course |
| `tool_lp_data_for_user_competency_summary_in_plan` | Get summary in plan |
| `tool_lp_data_for_user_evidence_list_page` | Get evidence list data |
| `tool_lp_data_for_user_evidence_page` | Get evidence page data |
| `tool_lp_list_courses_using_competency` | List courses using competency |
| `tool_lp_search_cohorts` | Search cohorts |
| `tool_lp_search_users` | Search users |
| `tool_policy_get_policy_version` | Get policy version |
| `tool_policy_submit_accept_on_behalf` | Accept policy on behalf |
| `tool_usertours_complete_tour` | Complete user tour |
| `tool_usertours_fetch_and_start_tour` | Fetch and start tour |
| `tool_usertours_reset_tour` | Reset user tour |
| `tool_usertours_step_shown` | Mark tour step shown |

> [!NOTE]
> This is not an exhaustive list of all 421 functions — some administrative, theme-specific, and less commonly used functions are not listed individually above. The full list can be obtained from `core_webservice_get_site_info` response → `functions` array.

---

## 14. Portal API vs Moodle WS — Comparison Table

| Feature | Portal API (sinhvien.ut.edu.vn) | Moodle WS API (courses.ut.edu.vn) |
|---|---|---|
| **Auth Method** | Cookie-based session + reCAPTCHA | Token-based (persistent) |
| **Token Persistence** | Session-bound (expires on logout/timeout) | **Persistent** — survives logout |
| **reCAPTCHA Required** | ✅ Yes (on login) | ❌ No |
| **Logout Risk** | ⚠️ Session invalidated if user logs out | ✅ None — token independent of session |
| **Endpoint Count** | ~10–15 endpoints | **421 functions** |
| **Session Management** | Manual cookie handling | Simple `wstoken` parameter |
| **Data Coverage** | Academic records, schedules, fees | Courses, grades, assignments, calendar, forums, quizzes |
| **Grade Detail** | Final transcript grades | Per-item breakdown (weight, raw, max, feedback) |
| **Real-time Events** | ❌ No | ✅ Calendar, upcoming, action events |
| **Assignment Tracking** | ❌ No | ✅ Full submission status, deadlines, files |
| **Completion Tracking** | ❌ No | ✅ Per-activity completion status |
| **Messaging** | ❌ No | ✅ Full messaging & notification system |
| **Rate Limiting** | Unknown | Minimal (standard Moodle limits) |
| **Mobile Support** | Web only | Designed for mobile apps |

> [!IMPORTANT]
> **Recommendation**: Use Moodle WS API as the **primary data source** for UTHelper. It provides more data, more reliable authentication, and no risk of session invalidation.

---

## 15. Feature Priority Table for UTHelper

| Priority | Feature | Primary API | Key Parameters | Notes |
|---|---|---|---|---|
| 🔴 P0 | **Authentication** | `GET /login/token.php` | `username`, `password`, `service` | One-time; store token persistently |
| 🔴 P0 | **User Identification** | `core_webservice_get_site_info` | — | Get `userid` for all subsequent calls |
| 🔴 P0 | **Upcoming Deadlines** | `core_calendar_get_calendar_upcoming_view` | — | Quiz opens, assignment due dates, attendance |
| 🔴 P0 | **Assignment Due Dates** | `mod_assign_get_assignments` | `courseids[]` | All assignments across enrolled courses |
| 🟠 P1 | **Grade Monitoring** | `gradereport_overview_get_course_grades` | `userid` | Quick grade check across all courses |
| 🟠 P1 | **Grade Details** | `gradereport_user_get_grade_items` | `courseid`, `userid` | Per-item breakdown per course |
| 🟠 P1 | **Enrolled Courses** | `core_enrol_get_users_courses` | `userid` | Get all 37 courses, filter by semester |
| 🟡 P2 | **Course Contents** | `core_course_get_contents` | `courseid` | Materials, sections, modules |
| 🟡 P2 | **Completion Tracking** | `core_completion_get_activities_completion_status` | `courseid`, `userid` | Per-activity completion |
| 🟡 P2 | **Notifications** | `message_popup_get_popup_notifications` | `useridto` | Check for new notifications |
| 🟢 P3 | **User Profile** | `core_user_get_users_by_field` | `field`, `values[]` | Display user info |
| 🟢 P3 | **Calendar — By Course** | `core_calendar_get_action_events_by_course` | `courseid` | Course-specific events |
| 🟢 P3 | **Calendar — Time Range** | `core_calendar_get_action_events_by_timesort` | `timesortfrom`, `timesortto` | Events in a time window |
| 🟢 P3 | **Quiz Attempts** | `mod_quiz_get_user_attempts` | `quizid`, `userid` | Check quiz attempt status |
| 🟢 P3 | **Submission Status** | `mod_assign_get_submission_status` | `assignid` | Whether user has submitted |
| 🔵 P4 | **Messaging** | `core_message_get_conversations` | — | User conversations |
| 🔵 P4 | **Forum Activity** | `mod_forum_get_forum_discussions` | `forumid` | Forum discussions |
| 🔵 P4 | **Badge Tracking** | `core_badges_get_user_badges` | `userid` | Achievement badges |

### Recommended Alert Workflow

```
1. Authenticate → store token
2. Get site_info → extract userid
3. Get enrolled courses → filter current semester (idnumber prefix)
4. For each current course:
   a. Get assignments → check duedates against now
   b. Get grade items → detect new grades
   c. Get completion status → find incomplete items
5. Get upcoming events → surface next 24-48 hours
6. Compare with last check → send alerts for changes
```

---

## 16. Technical Reference

### Request Format

All requests use the Moodle REST API format:

```
{BASE_URL}/webservice/rest/server.php?wstoken={TOKEN}&wsfunction={FUNCTION}&moodlewsrestformat=json&{PARAMS}
```

| Component | Value |
|---|---|
| `BASE_URL` | `https://courses.ut.edu.vn` |
| `TOKEN` | 32-character hex string from `/login/token.php` |
| `FUNCTION` | One of the 421 available functions |
| `moodlewsrestformat` | Always `json` (alternatives: `xml`) |

### HTTP Methods

- **GET**: All read operations support GET with query parameters.
- **POST**: Required for write operations. Parameters sent as `application/x-www-form-urlencoded`.
- Both GET and POST work for most read operations.

### Array Parameters

Moodle uses indexed notation for array parameters:

```
# Single value
courseids[0]=21252

# Multiple values
courseids[0]=21252&courseids[1]=21263&courseids[2]=21129
```

### Error Responses

#### Invalid Token

```json
{
  "exception": "moodle_exception",
  "errorcode": "invalidtoken",
  "message": "Invalid token - token not found"
}
```

#### Missing Required Parameter

```json
{
  "exception": "invalid_parameter_exception",
  "errorcode": "invalidparameter",
  "message": "Invalid parameter value detected"
}
```

#### Access Denied

```json
{
  "exception": "required_capability_exception",
  "errorcode": "nopermissions",
  "message": "Sorry, but you do not currently have permissions to do that (View participants)"
}
```

### Timestamp Format

All timestamps in the API are **Unix timestamps** (seconds since epoch, UTC).

```python
# Python conversion example
from datetime import datetime, timezone
timestamp = 1783270740
dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
# 2026-07-04 05:59:00 UTC
```

```javascript
// JavaScript conversion example
const timestamp = 1783270740;
const date = new Date(timestamp * 1000);
// Sat Jul 04 2026 12:59:00 GMT+0700
```

### Rate Limiting

Moodle 4.3.5 does not impose aggressive rate limiting on the WS API, but:

- Avoid more than **10 concurrent requests**.
- Batch course queries using array parameters (e.g., `courseids[0]...[N]`).
- Use `tool_mobile_call_external_functions` to batch multiple function calls in one HTTP request.
- Cache responses where appropriate (enrolled courses change infrequently).

### Moodle Version Details

| Property | Value |
|---|---|
| **Version** | 4.3.5 |
| **Build** | 20240610 |
| **Internal Version** | 2023100905 |
| **Theme** | Edly |
| **Calendar Type** | Gregorian |
| **Default Language** | Vietnamese (`vi`) |
| **Auth Plugin** | `uth` (custom UTH plugin) |
| **Site Name** | Elearning |
| **Site URL** | https://courses.ut.edu.vn |

---

## Appendix: Quick Reference — Essential URLs

| Purpose | URL Template |
|---|---|
| **Get Token** | `https://courses.ut.edu.vn/login/token.php?username={USER}&password={PASS}&service=moodle_mobile_app` |
| **API Call** | `https://courses.ut.edu.vn/webservice/rest/server.php?wstoken={TOKEN}&wsfunction={FUNC}&moodlewsrestformat=json` |
| **View Course** | `https://courses.ut.edu.vn/course/view.php?id={COURSEID}` |
| **View Assignment** | `https://courses.ut.edu.vn/mod/assign/view.php?id={CMID}` |
| **View Quiz** | `https://courses.ut.edu.vn/mod/quiz/view.php?id={CMID}` |
| **View Resource** | `https://courses.ut.edu.vn/mod/resource/view.php?id={CMID}` |
| **Download File** | `https://courses.ut.edu.vn/webservice/pluginfile.php/{CONTEXTID}/mod_resource/content/{ITEMID}/{FILENAME}?token={TOKEN}` |
| **Profile Image** | `https://courses.ut.edu.vn/theme/image.php/edly/core/{VERSION}/u/f1` |

---

*This documentation was generated from live API calls to courses.ut.edu.vn on 2026-06-22. All response examples contain real data.*
