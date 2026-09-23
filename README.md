# Data-Driven Employee Routing System

## Overview

The Data-Driven Employee Routing System is a corporate transport-management platform for planning and operating employee journeys in Dhaka. It brings together employee transport requests, fleet availability, driver assignments, and road-network-aware route planning in one system.

The platform supports the full journey from an employee requesting transport to an administrator approving demand, producing routes, assigning vehicles and drivers, and allowing drivers to manage the trip in progress. It is designed for recurring overnight shift operations as well as ad-hoc travel needs.

## What the system does

- Collects pickup and drop-off requests from employees.
- Supports weekly recurring transport preferences and time-sensitive ad-hoc requests.
- Organizes requests by service date, shift, and operating area.
- Plans pickup and drop-off routes across a complete overnight service period.
- Respects vehicle capacity, availability, service timing, route-duration limits, and assigned pickup locations.
- Uses Bangladesh road-network data to produce road-based distance, duration, and route geometry.
- Gives administrators a view of demand, fleet state, routes, stops, and assignments.
- Gives drivers their assigned trips, stop sequence, passenger list, vehicle information, and trip-progress actions.
- Gives employees visibility of their submitted requests, scheduled transport, and account information.

## People and responsibilities

### Employees

Employees can maintain their profile, submit pickup and drop-off locations, manage pending requests, provide weekly transport preferences, and submit qualifying ad-hoc requests. They can review their request history and transport schedule.

### Drivers

Drivers can view their own profile and vehicle, see their assigned trips and ordered stops, review the passenger list, begin and complete assignments, and record passenger boarding at a stop.

### Administrators

Administrators oversee the transport operation. Their responsibilities include managing employees, drivers, vehicles, and transport zones; reviewing and approving requests; generating routes; assigning routes; and reviewing schedules, routes, and fleet status.

## Transport planning model

The system treats a service date as one connected overnight operation rather than a collection of unrelated trips. This matters because a vehicle’s pickup work, ending location, availability, and passenger load affect what it can do later in the night.

Route planning accounts for:

- Available vehicles and seat capacity.
- Vehicle parking locations and assigned service areas.
- Driver and vehicle assignments.
- Employee pickup and drop-off locations.
- Shift start and end times that cross midnight.
- Fixed pickup points for applicable shifts.
- Shared main-road drop-off arrangements for applicable areas and times.
- Employee walking distance to a shared pickup point.
- Boarding and alighting time at stops.
- Maximum route-duration rules.
- Vehicle reuse between pickup and drop-off service.

When a request cannot be served within these rules, it is retained as an unassigned outcome with a reason rather than disappearing from the operational view.

## Main components

| Component | Purpose |
| --- | --- |
| Employee experience | Request transport, manage personal details, and view requests and schedules. |
| Driver experience | Review daily work, vehicle details, routes, stops, and passengers. |
| Administration workspace | Manage people, fleet, zones, requests, routes, assignments, and operational summaries. |
| Transport operations service | Applies transport rules and coordinates request, route, vehicle, and assignment information. |
| Routing engine | Produces road-based travel estimates and map-ready route paths from Bangladesh road data. |
| Data service | Stores users, employees, drivers, vehicles, zones, requests, routes, stops, passengers, and assignments. |
| Background operations | Identifies completed request periods and supports scheduled routing activity. |
| Deployment bundle | Runs the web application, operational service, and routing engine together as one deployable system. |

## Information managed by the platform

The platform maintains the operational information needed to run employee transport, including:

- User and role information.
- Employee and driver profiles.
- Vehicles, capacities, parking locations, and availability.
- Service zones and area descriptions.
- Fixed vehicle pickup locations.
- Pickup, drop-off, weekly, and ad-hoc transport requests.
- Route summaries, ordered stops, route geometry, and passenger-stop relationships.
- Driver route assignments and trip progress.
- Routing outcomes, including requests that could not be assigned.

## User journey

1. An employee submits or updates a transport requirement.
2. An administrator reviews the demand and approves or rejects individual requests where required.
3. The system combines eligible requests with fleet, zone, and scheduling information.
4. Routes are created for the service date, including stops, passengers, and assignments.
5. Administrators review routes and operational summaries and make assignments when needed.
6. Drivers follow their assigned route and record trip progress.
7. Employees and administrators can review the resulting schedule and request status.

## Bangladesh map data

The routing component is prepared with Bangladesh OpenStreetMap data. This enables the system to base journeys on the road network rather than direct-line estimates, providing realistic route paths, road distances, and travel durations for transport planning.

The source map and its prepared routing graph are operational assets rather than repository content. They are kept separately so the application remains manageable while the road graph can be refreshed independently when map data changes.

## Repository guide

| Area | Contents |
| --- | --- |
| `Frontend/` | The employee, driver, administrator, authentication, mapping, and shared user experiences. |
| `backend/` | The operational service, transport rules, data models, routing workflow, scheduling behavior, and validation coverage. |
| `osrm-data/` | The local location reserved for Bangladesh routing-map assets. |
| `docker-compose.yml` | The definition of the complete application stack. |
| `README-deploy.md` | Deployment-specific operational notes. |
| `render.yaml` | Alternative hosting configuration. |

## Project goals

The system is intended to make corporate transport operations more predictable, visible, and efficient by replacing disconnected requests and manual route decisions with a shared operational view. It prioritizes practical constraints—such as seat capacity, shifts, driver availability, and real roads—while giving each participant a focused experience for their role.

## Related documentation

- [Deployment guide](README-deploy.md)
- [Backend overview](backend/readme.md)
- [Frontend notes](Frontend/README.md)
- [Attributions](Frontend/ATTRIBUTIONS.md)

abc
