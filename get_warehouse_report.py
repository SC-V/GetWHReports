import datetime
import requests
import json
import pandas
from pytz import timezone
from googleapiclient import discovery
import io
import streamlit as st
import haversine as hs
import pydeck as pdk
import streamlit_analytics
import dateutil.parser

st.set_page_config(layout="wide")

CLAIM_SECRETS = st.secrets["CLAIM_SECRETS"]
SHEET_KEY = st.secrets["SHEET_KEY"]
SHEET_ID = st.secrets["SHEET_ID"]
API_URL = st.secrets["API_URL"]
FILE_BUFFER = io.BytesIO()

def calculate_distance(row):
    location_1 = (row["lat"], row["lon"])
    location_2 = (row["store_lat"], row["store_lon"])
    row["linear_distance"] = round(hs.haversine(location_1, location_2), 2)
    return row


def get_pod_orders():
    service = discovery.build('sheets', 'v4', discoveryServiceUrl=
    'https://sheets.googleapis.com/$discovery/rest?version=v4',
                              developerKey=SHEET_KEY)

    spreadsheet_id = SHEET_ID
    range_ = 'A:A'

    request = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_)
    response = request.execute()
    pod_orders = [item for sublist in response["values"] for item in sublist]
    return pod_orders


def check_for_pod(row, orders_with_pod):
    if row["status"] not in ["delivered", "delivered_finish"]:
        row["proof"] = "-"
        return row
    if str(row["client_id"]) in orders_with_pod:
        row["proof"] = "Proof provided"
    else:
        row["proof"] = "No proof"
    return row


def check_for_cod(row, orders_with_cod: dict):
    if row["price_of_goods"] < 1:
        row["cash_collected"] = "Prepaid"
        row["cash_prooflink"] = "Prepaid"
        return row
    if row["status"] not in ["delivered", "delivered_finish"]:
        row["cash_collected"] = "-"
        row["cash_prooflink"] = "-"
        return row
    if str(row["client_id"]) in orders_with_cod.keys():
        row["cash_collected"] = "Deposit verified"
        row["cash_prooflink"] = orders_with_cod[row["client_id"]]
    else:
        row["cash_collected"] = "Not verified"
        row["cash_prooflink"] = "No link"
    return row


def get_claims(secret, date_from, date_to, cursor=0):
    url = API_URL
    timezone_offset = "-06:00"
    payload = json.dumps({
        "created_from": f"{date_from}T00:00:00{timezone_offset}",
        "created_to": f"{date_to}T23:59:59{timezone_offset}",
        "limit": 1000,
        "cursor": cursor
    }) if cursor == 0 else json.dumps({"cursor": cursor})

    headers = {
        'Content-Type': 'application/json',
        'Accept-Language': 'en',
        'Authorization': f"Bearer {secret}"
    }

    response = requests.request("POST", url, headers=headers, data=payload)
    claims = json.loads(response.text)
    cursor = None
    try:
        cursor = claims['cursor']
        print(f"CURSOR: {cursor}")
    except:
        print("LAST PAGE PROCESSED")
    try:
        return claims['claims'], cursor
    except:
        return [], None


def get_report(option="Today", start_=None, end_=None) -> pandas.DataFrame:
    
    offset_back = 0
    if option == "Yesterday":
        offset_back = 1
    elif option == "Tomorrow":
        offset_back = -1
    elif option == "Received":
        offset_back = 0

    client_timezone = "America/Mexico_City"

    if option == "Monthly":
        start_ = "2023-04-01"
        end_ = "2023-04-30"
        today = datetime.datetime.now(timezone(client_timezone))
        date_from_offset = datetime.datetime.fromisoformat(start_).astimezone(
            timezone(client_timezone)) - datetime.timedelta(days=2)
        date_from = date_from_offset.strftime("%Y-%m-%d")
        date_to = end_
    elif option == "Received":
        today = datetime.datetime.now(timezone(client_timezone)) - datetime.timedelta(days=offset_back)
        search_from = today.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=5)
        search_to = today.replace(hour=23, minute=59, second=59, microsecond=999999) + datetime.timedelta(days=6)
        date_from = search_from.strftime("%Y-%m-%d")
        date_to = search_to.strftime("%Y-%m-%d")        
    else:
        today = datetime.datetime.now(timezone(client_timezone)) - datetime.timedelta(days=offset_back)
        search_from = today.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=3)
        search_to = today.replace(hour=23, minute=59, second=59, microsecond=999999)
        date_from = search_from.strftime("%Y-%m-%d")
        date_to = search_to.strftime("%Y-%m-%d")

    today = today.strftime("%Y-%m-%d")
    report = []
    for secret in CLAIM_SECRETS:
        claims, cursor = get_claims(secret, date_from, date_to)
        while cursor:
            new_page_claims, cursor = get_claims(date_from, date_to, cursor)
            claims = claims + new_page_claims
        for claim in claims:
            try:
                claim_from_time = claim['same_day_data']['delivery_interval']['from']
            except:
                continue
            cutoff_time = datetime.datetime.fromisoformat(claim_from_time).astimezone(timezone(client_timezone))
            cutoff_date = cutoff_time.strftime("%Y-%m-%d")
            if not start_ and option != "Received":
                if cutoff_date != today:
                    continue
            report_cutoff = cutoff_time.strftime("%Y-%m-%d %H:%M")
            try:
                report_client_id = claim['route_points'][1]['external_order_id']
            except:
                report_client_id = "External ID not set"
            report_claim_id = claim['id']
            try:
                report_lo_code = claim['items'][0]['extra_id']
            except:
                report_lo_code = "No LO code"
            report_pickup_address = claim['route_points'][0]['address']['fullname']
            report_corp_id = claim['corp_client_id']
            report_pod_point_id = claim['route_points'][1]['id']
            report_receiver_address = claim['route_points'][1]['address']['fullname']
            report_receiver_phone = claim['route_points'][1]['contact']['phone']
            report_receiver_name = claim['route_points'][1]['contact']['name']
            report_status = claim['status']
            report_created_time = dateutil.parser.isoparse(claim['created_ts']).astimezone(timezone(client_timezone))
            report_status_time = dateutil.parser.isoparse(claim['updated_ts']).astimezone(timezone(client_timezone))
            report_store_name = claim['route_points'][0]['contact']['name']
            report_longitude = claim['route_points'][1]['address']['coordinates'][0]
            report_latitude = claim['route_points'][1]['address']['coordinates'][1]
            report_store_longitude = claim['route_points'][0]['address']['coordinates'][0]
            report_store_latitude = claim['route_points'][0]['address']['coordinates'][1]
            try:
                report_courier_name = claim['performer_info']['courier_name']
                report_courier_park = claim['performer_info']['legal_name']
            except:
                report_courier_name = "No courier yet"
                report_courier_park = "No courier yet"
            try:
                report_return_reason = str(claim['route_points'][1]['return_reasons'])
                report_return_comment = str(claim['route_points'][1]['return_comment'])
            except:
                report_return_reason = "No return reasons"
                report_return_comment = "No return comments"
            try:
                report_autocancel_reason = claim['autocancel_reason']
            except:
                report_autocancel_reason = "No cancel reasons"
            try:
                report_route_id = claim['route_id']
            except:
                report_route_id = "No route"
            try:
                report_price_of_goods = 0
                for item in claim['items']:
                    report_price_of_goods += float(item['cost_value'])
            except:
                report_price_of_goods = 0
            row = [report_cutoff, report_client_id, report_claim_id, report_pod_point_id, report_lo_code,
                   report_pickup_address, report_receiver_address, report_receiver_phone, report_receiver_name,
                   report_status, report_status_time, report_created_time, report_store_name, report_courier_name, report_courier_park,
                   report_return_reason, report_return_comment, report_autocancel_reason, report_route_id,
                   report_longitude, report_latitude, report_store_longitude, report_store_latitude, report_price_of_goods, report_corp_id]
            report.append(row)

    result_frame = pandas.DataFrame(report,
                                    columns=["cutoff", "client_id", "claim_id", "pod_point_id", "lo_code",
                                             "pickup_address", "receiver_address", "receiver_phone",
                                             "receiver_name", "status", "status_time", "created_time",
                                             "store_name", "courier_name", "courier_park",
                                             "return_reason", "return_comment", "cancel_comment",
                                             "route_id", "lon", "lat", "store_lon", "store_lat", "price_of_goods", "corp_id"])
    orders_with_pod = get_pod_orders()
    result_frame = result_frame.apply(lambda row: calculate_distance(row), axis=1)
    result_frame = result_frame.apply(lambda row: check_for_pod(row, orders_with_pod), axis=1)
    try:
        result_frame.insert(3, 'proof', result_frame.pop('proof'))
    except:
        print("No proofs yet")
    return result_frame


streamlit_analytics.start_tracking()
st.markdown(f"# Warehouse routes report")
st.caption(f"For now use Yesterday option for tracking NDD orders.")

if st.sidebar.button("Refresh data", type="primary"):
    st.experimental_memo.clear()
st.sidebar.caption(f"Page reload doesn't refresh the data.\nInstead, use this button to get a fresh report")

option = st.sidebar.selectbox(
    "Select report date:",
    ["Today", "Yesterday", "Tomorrow", "Monthly", "Received"]
)


@st.experimental_memo
def get_cached_report(option):
    report = get_report(option)
    return report


df = get_cached_report(option)        
delivered_today = len(df[df['status'].isin(['delivered', 'delivered_finish'])])

statuses = st.sidebar.multiselect(
    'Filter by status:',
    ['delivered',
     'pickuped',
     'returning',
     'cancelled_by_taxi',
     'delivery_arrived',
     'cancelled',
     'performer_lookup',
     'performer_found',
     'performer_draft',
     'returned_finish',
     'performer_not_found',
     'return_arrived',
     'delivered_finish',
     'failed',
     'accepted',
     'new',
     'pickup_arrived'])

couriers = st.sidebar.multiselect(
    "Filter by courier:",
    df["courier_name"].unique()
)

only_no_proofs = st.sidebar.checkbox("Only parcels without proofs")

if only_no_proofs:
    df = df[df["proof"] == "No proof"]

without_cancelled = st.sidebar.checkbox("Without cancels")

if without_cancelled:
    df = df[~df["status"].isin(["cancelled", "performer_not_found", "failed", "cancelled_by_taxi"])]

if option != "Received":
    col1, col2, col3 = st.columns(3)
    col1.metric(f"Delivered {option.lower()} :package:", delivered_today)

if not statuses or statuses == []:
    filtered_frame = df
else:
    filtered_frame = df[df['status'].isin(statuses)]

if couriers:
    filtered_frame = df[df['courier_name'].isin(couriers)]

if option == "Received":
    filtered_frame = filtered_frame[filtered_frame['status'].isin(["performer_lookup"])]
    
st.dataframe(filtered_frame)

client_timezone = "America/Mexico_City"
TODAY = datetime.datetime.now(timezone(client_timezone)).strftime("%Y-%m-%d") \
    if option == "Today" \
    else datetime.datetime.now(timezone(client_timezone)) - datetime.timedelta(days=1)

st.caption(
    f'Total of :blue[{len(filtered_frame)}] orders in the table.')

with st.expander(":round_pushpin: Orders on a map:"):
    st.caption(
        f'Hover order to see details. :green[Green] orders are delivered, and :red[red] – are the in delivery state. :orange[Orange] are returned or returning. Gray are cancelled.')
    chart_data_delivered = filtered_frame[filtered_frame["status"].isin(['delivered', 'delivered_finish'])]
    chart_data_in_delivery = filtered_frame[~filtered_frame["status"].isin(
        ['delivered', 'delivered_finish', 'cancelled', 'cancelled_by_taxi', 'returning', 'returned_finish',
         'return_arrived'])]
    chart_data_returns = filtered_frame[
        filtered_frame["status"].isin(['returning', 'returned_finish', 'return_arrived'])]
    chart_data_cancels = filtered_frame[filtered_frame["status"].isin(['cancelled', 'cancelled_by_taxi'])]
    try:
        view_state_lat = filtered_frame['lat'].iloc[0]
        view_state_lon = filtered_frame['lon'].iloc[0]
    except:
        view_state_lat = 19.4285
        view_state_lon = -99.1277
    filtered_frame['cutoff'] = filtered_frame['cutoff'].str.split(' ').str[1]
    stores_on_a_map = filtered_frame.groupby(['store_name', 'store_lon', 'store_lat'])['cutoff'].agg(
        lambda x: ', '.join(x.unique())).reset_index(drop=False)
    stores_on_a_map.columns = ['store_name', 'store_lon', 'store_lat', 'cutoff']
    st.pydeck_chart(pdk.Deck(
        map_style=None,
        height=1200,
        initial_view_state=pdk.ViewState(
            latitude=view_state_lat,
            longitude=view_state_lon,
            zoom=10,
            pitch=0,
        ),
        tooltip={"text": "{store_name} : {cutoff}\n{courier_name} : {status}\n{client_id} : {claim_id}"},
        layers=[
            pdk.Layer(
                'ScatterplotLayer',
                data=chart_data_delivered,
                get_position='[lon, lat]',
                get_color='[11, 102, 35, 160]',
                get_radius=200,
                pickable=True
            ),
            pdk.Layer(
                'ScatterplotLayer',
                data=chart_data_in_delivery,
                get_position='[lon, lat]',
                get_color='[200, 30, 0, 160]',
                get_radius=200,
                pickable=True
            ),
            pdk.Layer(
                'ScatterplotLayer',
                data=chart_data_cancels,
                get_position='[lon, lat]',
                get_color='[215, 210, 203, 200]',
                get_radius=200,
                pickable=True
            ),
            pdk.Layer(
                'ScatterplotLayer',
                data=chart_data_returns,
                get_position='[lon, lat]',
                get_color='[237, 139, 0, 160]',
                get_radius=200,
                pickable=True
            ),
            pdk.Layer(
                'ScatterplotLayer',
                data=filtered_frame,
                get_position='[store_lon, store_lat]',
                get_color='[0, 128, 255, 160]',
                get_radius=250,
                pickable=True
            )
        ],
    ))

with pandas.ExcelWriter(FILE_BUFFER, engine='xlsxwriter') as writer:
    filtered_frame["status_time"] = filtered_frame["status_time"].apply(lambda a: pandas.to_datetime(a).date()).reindex()
    filtered_frame["created_time"] = filtered_frame["created_time"].apply(lambda a: pandas.to_datetime(a).date()).reindex()
    filtered_frame.to_excel(writer, sheet_name='wh_routes_report')
    writer.save()

    st.download_button(
        label="Download report as xlsx",
        data=FILE_BUFFER,
        file_name=f"route_report_{TODAY}.xlsx",
        mime="application/vnd.ms-excel"
    )
    
streamlit_analytics.stop_tracking()
