# Vegvesen DATEX II 3.1 Reference

## Overview

Statens vegvesen publishes authenticated DATEX II 3.1 pull services for:

- traffic situations
- travel times
- travel time route metadata
- roadside weather observations
- weather station tables
- CCTV site metadata
- CCTV status

HTTP GET returns XML. SOAP and WSDL are also available for each service.

## Access And Transport

- Production base URL: `https://datex-server-get-v3-1.atlas.vegvesen.no/`
- Authentication: Basic Auth
- Access model: Vegvesenet creates a username and password per client after registration
- Conditional polling:
  - request header `If-Modified-Since`
  - response header `Last-Modified`
  - `304 Not Modified` when no new data exists
- Delivery mode: pull only

## Services

### Situations

Use for traffic messages such as closures, road works, accidents, rerouting, difficult conditions, and temporary regulations.

- HTTP GET:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetSituation/pullsnapshotdata`
- SOAP:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetSituation`
- WSDL:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetSituation?wsdl`
- Update cadence: continuously published

Filtering:

- Filter path:
  `.../GetSituation/pullsnapshotdata/filter/<SituationRecordType>`
- SRTI flag:
  `.../GetSituation/pullsnapshotdata?srti=True`

Filter types from the 3.1 spec:

- `AbnormalTraffic`
- `Accident`
- `Activity`
- `AnimalPresenceObstruction`
- `AuthorityOperation`
- `Conditions`
- `ConstructionWorks`
- `DisturbanceActivity`
- `EnvironmentalObstruction`
- `EquipmentOrSystemFault`
- `GeneralInstructionOrMessageToRoadUsers`
- `GeneralNetworkManagement`
- `GeneralObstruction`
- `GenericSituationRecord`
- `InfrastructureDamageObstruction`
- `MaintenanceWorks`
- `NetworkManagement`
- `NonWeatherRelatedRoadConditions`
- `PoorEnvironmentConditions`
- `PublicEvent`
- `ReroutingManagement`
- `RoadOrCarriagewayOrLaneManagement`
- `RoadsideAssistance`
- `ServiceDisruption`
- `SpeedManagement`
- `TransitInformation`
- `VehicleObstruction`
- `WeatherRelatedRoadConditions`
- `WinterDrivingManagement`

Examples:

```text
https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetSituation/pullsnapshotdata
https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetSituation/pullsnapshotdata/filter/Accident
https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetSituation/pullsnapshotdata?srti=True
```

Versioning notes:

- Use `situationRecordVersionTime` to determine the latest record version.
- If `overallEndTime` has passed, the event can normally be treated as expired.
- If `overallEndTime` is missing, treat the record as valid until further notice.

### Travel Times

Travel time data covers predefined segments around Oslo, Bergen, Stavanger, Kristiansand, Trondheim, plus E18 Oslo to Aust-Agder and E6 As to Kolomoen.

Travel time routes:

- HTTP GET:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetPredefinedTravelTimeLocations/pullsnapshotdata`
- SOAP:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetPredefinedTravelTimeLocations`
- WSDL:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetPredefinedTravelTimeLocations?wsdl`

Travel time data:

- HTTP GET:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetTravelTimeData/pullsnapshotdata`
- SOAP:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetTravelTimeData`
- WSDL:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetTravelTimeData?wsdl`

Update cadence:

- Spec PDF: every 5 minutes
- Main DATEX overview page also says every 5 minutes
- Some Dataportalen API pages currently show every 5 seconds. Treat that as a likely page inconsistency unless live service behavior proves otherwise.

### Road Weather

Measurements:

- HTTP GET:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetMeasuredWeatherData/pullsnapshotdata`
- SOAP:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetMeasuredWeatherData`
- WSDL:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetMeasuredWeatherData?wsdl`

Station table:

- HTTP GET:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetMeasurementWeatherSiteTable/pullsnapshotdata`
- SOAP:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetMeasurementWeatherSiteTable`
- WSDL:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetMeasurementWeatherSiteTable?wsdl`

Update cadence:

- weather measurements every 10 minutes

### CCTV

Site metadata:

- HTTP GET:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetCCTVSiteTable/pullsnapshotdata`
- SOAP:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetCCTVSiteTable`
- WSDL:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetCCTVSiteTable?wsdl`

Status:

- HTTP GET:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetCCTVStatus/pullsnapshotdata`
- SOAP:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetCCTVStatus`
- WSDL:
  `https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetCCTVStatus?wsdl`

Notes:

- Site table gives location and image or video references.
- Status indicates whether a camera is active or inactive.
- Camera update frequency varies by camera type and connectivity.

## HTTP Status Handling

- `200 OK`: successful request
- `200 OK` with "Delivery break" in the message container: upstream source has not delivered new data; publication may be stale
- `304 Not Modified`: no new entries since the specified `If-Modified-Since`
- `400 Bad Request`: invalid request such as unsupported filter or invalid values
- `401 Unauthorized`: invalid authentication
- `403 Forbidden`: nonexistent endpoint, invalid token, or missing rights
- `404 Not Found`: no entry with the given id when using id-based lookup
- `500 Internal Server Error`: server-side failure

## Official Sources

- DATEX 3.1 repository:
  `https://git.vegvesen.no/projects/DATEX2/repos/datex2-spesifications/browse/3.1`
- DATEX 3.1 specification PDF:
  `https://git.vegvesen.no/projects/DATEX2/repos/datex2-spesifications/raw/3.1/oldVersions/NPRA_DATEXII_3_1_Specification_v2.1.pdf`
- DATEX overview page:
  `https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/datex/what-is-datex/?lang=en`
- Request access page:
  `https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/what-is-datex/get-access/`
- Traffic messages API page:
  `https://dataut.vegvesen.no/en/dataservice/trafikkmeldinger-api`
- Travel times routes API page:
  `https://dataut.vegvesen.no/en/dataservice/reisetider-reiser-api`
- Travel times locations API page:
  `https://dataut.vegvesen.no/en/dataservice/reisetider-lokasjoner-api`
- Weather measurements API page:
  `https://dataut.vegvesen.no/dataservice/vaerdata-malinger-api`
- Weather station sites API page:
  `https://dataut.vegvesen.no/en/dataservice/vaerdata-malestasjoner-api`
- CCTV updates API page:
  `https://dataut.vegvesen.no/en/dataservice/webkamera-oppdateringer-api`
- CCTV status API page:
  `https://dataut.vegvesen.no/dataservice/webkamera-statuser-api`
- DATEX standard:
  `https://www.datex2.eu/`
