# -*- coding:utf-8 -*-
import os
import boto3
import logging
import http.client
from datetime import datetime, timedelta
import json
import csv
import pytz
import dateutil.tz
import urllib.parse
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TRN, MEM = "transactions", "memberships"

AWS_REGION = os.environ.get('REGION')
SENDER = os.environ.get('SENDER')
RECIPIENT = os.environ.get('RECIPIENT')
SUBJECT = os.environ.get('SUBJECT')
CHARSET = "utf-8"
ses = boto3.client('ses', region_name=AWS_REGION)

utc_tz = pytz.timezone('UTC')
local_tz = pytz.timezone('Europe/Berlin')


def run(event, context):
    secret = get_secret()      # Fetch secrets to use a Qonto API
    dates, fdates = get_date()  # Specify the period of filters for a Qonto request

    # Get Pending/Declined transactions and if exists, display them in the log and email message
    filters = filter(fdates, "update")
    data = get_qonto(TRN, filters, secret)
    updates_msg = log_updates(data)

    # Get member data and Completed transactions
    data_mem = get_qonto(MEM, filters, secret)
    filters = filter(fdates, "settle")
    data_set = get_qonto(TRN, filters, secret)
    transactions = get_completes(data_mem, data_set)

    if transactions:
        try:
            # Produce a csv file on Lambda
            file_path = '/tmp/' + "qonto_" + dates[0] + "_" + dates[1] + ".csv"
            with open(file_path, 'w', newline='') as csvFile:
                csvwriter = csv.writer(
                    csvFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_NONNUMERIC)
                csvwriter.writerow(
                    ['Buchungsdatum', 'Auftraggeber/Empfänger', 'Verwendungszweck', 'Betrag', 'Zusatzinfo'])
                for record in transactions:
                    csvwriter.writerow(record)
        except Exception as error:
            logger.error(error)

        logger.info(
            "The csv file has been successfully generated: " + file_path)
        body = "Hello,\r\n\r\nThe bank transactions of your Qonto accounts for the last week are now ready.\r\nPlease see the attached CSV."
    else:
        body = "Hello,\r\n\r\nThere are no bank transactions of your Qonto accounts for the last week.\r\n"

    if updates_msg:
        body = body + "\r\n\r\n" + '\r\n'.join(updates_msg)

    # Send an email
    send_raw_email(SENDER, RECIPIENT, SUBJECT, body, CHARSET, file_path)


def get_secret():

    return {
        'secret-key': os.environ.get('QONTO_API_KEY'),
        'login': os.environ.get('QONTO_SLUG'),
        'iban': os.environ.get('QONTO_IBAN'),
    }


def get_date():
    """
    Set a week ago at 0:00:00 at start date and yesterday at 23:59:59 as end date,
    convert them from local timezone to UTC, and encode them.

    Returns:
        dates (list) : start/end datetime (YYYY-MM-DD)
        filder_dates (list) : start/end datetime (YYYY-MM-DDThh:mm:ss.sss.Z)
    """
    now = datetime.now(local_tz)  # today in local timezone
    start_datetime = now.replace(
        hour=0, minute=0, second=0, microsecond=0) + timedelta(days=-7)
    end_datetime = now.replace(
        hour=23, minute=59, second=59, microsecond=999999) + timedelta(days=-1)
    dates = []
    filter_dates = []
    for dt in start_datetime, end_datetime:
        date = dt.strftime('%Y-%m-%d')
        dates.append(date)
        fdate = utc_tz.normalize(dt).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        endate = urllib.parse.quote(fdate)
        filter_dates.append(endate)
    return dates, filter_dates


def log_updates(data):
    num = data['meta']['total_count']
    update_msg = []
    msg = "There are {} non-settled records.".format(num)
    update_msg.append(msg)
    logger.info(msg)
    for trn in data["transactions"]:
        status = trn['status']        # status
        date_utc = trn['updated_at']  # last updated date
        amt = trn['amount']           # amount
        side = trn['side']            # credit or debit
        lab = trn['label']            # counterpart
        ref = trn['reference']        # reference
        # Convert date and amount
        date = conv_utc(date_utc)
        amount = conv_amount(side, amt)
        # Log the results
        msg = "[{} {}] Couterpart: {}, Amount: {} EUR, Reference: {}".format(
            status, date, lab, amount, ref)
        logger.warning(msg)
        update_msg.append(msg)
    return update_msg


def get_qonto(typ, filters, secret):
    """
    Get Qonto records with the specified parameters and (if applicable) filters.

    Args:
        typ (str): "transactions" or "memberships"
        filters (str): filters of a request; applicable only for trn and consisted by status and dates
        secret (array)
    Returns:
        output: json output of the fetched data
    """
    seckey = secret['secret-key']
    login = secret['login']
    iban = secret['iban']
    payload = "{}"
    headers = {'authorization': login + ":" + seckey}
    param = typ  # for transactions, addtl filters in the parameter
    if typ == TRN:
        param = param + "?iban=" + iban + "&slug=" + login + filters

    try:
        conn = http.client.HTTPSConnection("thirdparty.qonto.com")
        conn.request("GET", f"/v2/{param}", payload, headers)
    except Exception as e:
        logger.error(e)
        logger.error(
            "Error occurred while requesting {} to Qonto. Filters: {}".format(typ, filters))
        raise e

    res = conn.getresponse()
    data = res.read()
    output = json.loads(data.decode("utf-8"))
    return output


def send_raw_email(src, to, sbj, body, char, file):
    logger.info('send_raw_email: START')
    msg = MIMEMultipart()
    msg['Subject'] = sbj
    msg['From'] = src
    msg['To'] = to
    msg_body = MIMEText(body.encode(char), 'plain', char)
    msg.attach(msg_body)

    att = MIMEApplication(open(file, 'rb').read())
    att.add_header('Content-Disposition', 'attachment',
                   filename=os.path.basename(file))
    msg.attach(att)

    try:
        response = ses.send_raw_email(
            Source=src,
            Destinations=[to],
            RawMessage={
                'Data': msg.as_string()
            }
        )
    except ClientError as e:
        logger.error(e.response['Error']['Message'])
    else:
        logger.info("Email sent! Message ID:"),
        logger.info(response['MessageId'])


def filter(dates, status):
    """
    Return filter parameters with a status, the specified dates and a date filter option,
    which is decided based on the status.

    Args:
            dates (list): encoded start/end datetime in UTC
            status (str): "update" for declined/pending, or "settle" for completed
    Returns:
            filters: The filter of status and period (&status[]=...)
    """
    if status == "update":
        status_fil = "&status[]=declined&status[]=pending"
        date_pre = "&updated_at_"
    elif status == "settle":
        status_fil = "&status[]=completed"
        date_pre = "&settled_at_"
    sdate_fil = date_pre + "from=" + dates[0]
    edate_fil = date_pre + "to=" + dates[1]
    filters = status_fil + sdate_fil + edate_fil
    return filters


def log_updates(data):
    num = data['meta']['total_count']
    logger.info("There are {} non-settled records.".format(num))

    for trn in data["transactions"]:
        status = trn['status']        # status
        date_utc = trn['updated_at']  # last updated date
        amt = trn['amount']           # amount
        side = trn['side']            # credit or debit
        lab = trn['label']            # counterpart
        ref = trn['reference']        # reference
        # Convert date and amount
        date = conv_utc(date_utc)
        amount = conv_amount(side, amt)
        # Log the results
        logger.info("{} (Last update: {})".format(status, date))
        logger.info("Counterpart : " + lab)
        logger.info("Amount      : " + amount + " EUR")
        if ref:
            logger.info("Reference   : " + ref)


def get_completes(data_mem, data_set):
    # Get the number of completed transactions
    num = data_set["meta"]["total_count"]
    logger.info("{} transactions found.".format(num))
    if num == 0:
        return

    # Create a dictionary of members
    members = {}
    for mem in data_mem['memberships']:
        member_id = mem['id']
        fname = mem['first_name']
        lname = mem['last_name']
        fullname = fname + " " + lname
        members.update({member_id: fullname})

    # Set an array to insert values for CSV
    trns = []
    for trn in data_set["transactions"]:
        amt = trn['amount']                # Betrag
        lamt = trn['local_amount']         # Zusatzinfo (foreign amount)
        side = trn['side']                 # Betrag (credit/debit)
        # Zusatzinfo (card/transfer/qonto_fee etc)
        op_type = trn['operation_type']
        lcur = trn['local_currency']       # Zusatzinfo (foreign currency)
        lab = trn['label']                 # Auftraggeber&Empfaenger
        book_date_utc = trn['settled_at']  # Buchungsdatum
        # value_date_utc = trn['emitted_at'] # Wertstellungsdatum
        note = trn['note']                 # [opt]Zusatzinfo (user's note)
        ref = trn['reference']             # [opt]Verwendungszweck
        user_id = trn['initiator_id']      # [opt]Zusatzinfo (user id)
        # Convert date and amount
        book_date = conv_utc(book_date_utc)
        # value_date = conv_utc(value_date_utc)
        amount = conv_amount(side, amt)

        # Reference
        if not ref:
            ref = op_type
            if user_id:
                user_name = members[user_id]
                ref = ref + " " + user_name
        # Additional Info (foreign transactions and/or note, or inserting a letter to prevent a record from falling out)
        l = []
        if lcur != "EUR":
            lamount = conv_amount(side, lamt)
            local = lamount + " " + lcur
            l.append(local)
        if note:
            l.append(note)
        addinfo = ' '.join(l)
        if not addinfo:
            addinfo = "_"

        trns.append([book_date, lab, ref, amount, addinfo])
    return trns


def conv_utc(date):
    """
    Convert the UTC timestamp in fetched records to the timestamp in the local timezone.

    Args:
        date (str): UTC timestamp (YYYY-MM-DDTHH:mm:ss.sssZ)
    Returns:
        date_local: Date converted to local TZ (YYYY-MM-DD HH:mm:ss)
    """
    dt_utc = utc_tz.localize(datetime.strptime(date, '%Y-%m-%dT%H:%M:%S.%fZ'))
    dt_local = local_tz.normalize(dt_utc)
    date_local = dt_local.strftime('%Y-%m-%d %H:%M:%S')
    return date_local


def conv_amount(side, amount):
    """
    Convert positive amount to the amount with positive/negative side.

    Args:
            side: "credit" or "debit"
            amount (float): positive amount

    Returns:
            amount (str): amount with positive/negative side
    """

    if (side == "debit"):
        amount = amount * -1
    return str(amount)
