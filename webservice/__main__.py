import json
import os
import random
import time

import nexmo
from aiohttp import web

routes = web.RouteTableDef()

MUSIC_WHILE_YOU_WAIT = [
    "https://assets.ctfassets.net/j7pfe8y48ry3/530pLnJVZmiUu8mkEgIMm2/dd33d28ab6af9a2d32681ae80004886e/oaklawn-dreams.mp3",
    "https://assets.ctfassets.net/j7pfe8y48ry3/2toXv1xuOsMm0Yku0YEGya/a792ce81a7866fc77f6768d416018012/broken-shovel.mp3",
    "https://assets.ctfassets.net/j7pfe8y48ry3/16VJzaewWsKWg4GsSUiwGi/9b715be5e8c850e46de98b64e6d31141/lennys-song.mp3",
    "https://assets.ctfassets.net/j7pfe8y48ry3/1qApZVYkxaiayA6aysGAOo/8983586c8ab4db8b69490718469a12f5/new-juno.mp3",
    "https://assets.ctfassets.net/j7pfe8y48ry3/6iXXKtJCp2oCMiGmsmAKqu/8163a8fe863405292ba3609193593add/davis-square-shuffle.mp3",
]


def get_nexmo_client():
    """Return an instance of Nexmo client library"""
    api_key = os.environ.get("NEXMO_API_KEY")
    api_secret = os.environ.get("NEXMO_API_SECRET")
    app_id = os.environ.get("NEXMO_APP_ID")
    private_key = os.environ.get("NEXMO_PRIVATE_KEY_VOICE_APP")

    client = nexmo.Client(
        key=api_key, secret=api_secret, application_id=app_id, private_key=private_key
    )
    return client


def get_phone_numbers():
    """Get the phone numbers from environment variables.

    Example:
    [
        {
            "name": "Mariatta",
            "phone": "16040001234"
        },
        {
            "name": "Miss Islington",
            "phone": "17782223333"
        }
    ]
    """
    return json.loads(os.environ.get("PHONE_NUMBERS"))


def get_phone_number_owner(phone_number):
    """Return the name of the phone number owner for the given phone number
    """
    phone_numbers = get_phone_numbers()
    for phone_number_info in phone_numbers:
        if phone_number_info["phone"] == phone_number:
            return phone_number_info["name"]

    return None


def get_hotline_description():
    """Return the description of this hotline, e.g: CoC hotline, or Head office"""
    return os.environ.get("HOTLINE_DESC")


def is_auto_recording():
    autorecord_flag = os.environ.get("AUTO_RECORD", "false")
    return autorecord_flag.lower() == "true"


@routes.get("/webhook/answer/")
async def answer_call(request):
    """Webhook event for answering incoming call to the hotline.

    Return the NCCO:
    - talk, indicate that this is the Code of Conduct hotline, and whether this call is recorded
    - connect the caller to a conference call (a conversation)
    - play music while the called is waiting to be connected
    - record the call (if environment variable is set)

    Dial everyone on staff, adding them to the same conversation

    """
    hotline_number = request.rel_url.query["to"]
    conversation_uuid = request.rel_url.query["conversation_uuid"].strip()
    call_uuid = request.rel_url.query["uuid"].strip()
    greeting = f"You've reached the {get_hotline_description()}."

    conversation_ncco = {
        "action": "conversation",
        "name": conversation_uuid,
        # "eventMethod": "POST",
        "musicOnHoldUrl": [random.choice(MUSIC_WHILE_YOU_WAIT)],
        "endOnExit": False,
        "startOnEnter": False,
    }

    # print(conversation_uuid)

    if is_auto_recording():
        greeting = f"{greeting} This call is recorded."
        conversation_ncco.update(
            {
                "record": True,
                "eventUrl": [
                    os.environ.get("ZAPIER_CATCH_HOOK_RECORDING_FINISHED_URL")
                ],
            }
        )

    ncco = [{"action": "talk", "text": greeting}, conversation_ncco]

    client = get_nexmo_client()
    phone_numbers = get_phone_numbers()

    for phone_number_dict in phone_numbers:
        # print(phone_number_dict)
        client.create_call(
            {
                "to": [{"type": "phone", "number": phone_number_dict["phone"]}],
                "from": {"type": "phone", "number": hotline_number},
                "answer_url": [
                    f"http://{request.host}/webhook/answer_conference_call/{conversation_uuid}/{call_uuid}/"
                ],
                "machine_detection": "hangup",
            }
        )

    return web.json_response(ncco)


@routes.get(
    "/webhook/answer_conference_call/{origin_conversation_uuid}/{origin_call_uuid}/"
)
async def answer_conference_call(request):
    """Webhook event when a conference staff answered the conference call.

    Notify the original caller that a staff is answering the call.

    Return the NCCO:
    - talk: indicate that they're being connected to the PyCascades Hotline
    - make the staff the moderator of the conference call (call will end when they hang up)
    """
    to_phone_number = request.rel_url.query["to"]
    origin_conversation_uuid = request.match_info["origin_conversation_uuid"]
    origin_call_uuid = request.match_info["origin_call_uuid"]

    phone_number_owner = get_phone_number_owner(to_phone_number)
    client = get_nexmo_client()

    try:
        response = client.send_speech(
            origin_call_uuid, text=f"{phone_number_owner} is joining this call."
        )
    except nexmo.Error as er:  # pragma: no cover
        print(
            f"error sending speech to {origin_call_uuid}, owner is {phone_number_owner}"
        )
        print(er)

    else:
        print(f"Successfully notified caller. {response}")

    ncco = [
        {
            "action": "talk",
            "text": f"Hello {phone_number_owner}, connecting you to {get_hotline_description()}.",
        },
        {
            "action": "conversation",
            "name": origin_conversation_uuid,
            "startOnEnter": True,
            "endOnExit": True,
        },
    ]

    return web.json_response(ncco)


@routes.get("/webhook/inbound-sms/")
async def inbound_sms(request):
    """Webhook event that receives and inbound SMS messages and notifies all
    staff.

    It also sends the sender an acknowledgment.

    This should be configured in Nexmo to send using GET.
    """
    hotline_number = request.rel_url.query["to"]
    from_number = request.rel_url.query["msisdn"]
    message = request.rel_url.query["text"]

    client = get_nexmo_client()
    phone_numbers = get_phone_numbers()

    for phone_number_dict in phone_numbers:
        client.send_message(
            {
                # Send from the number the received this message.
                "from": hotline_number,
                "to": phone_number_dict["phone"],
                "text": f"{from_number}: {message}",
            }
        )

    # Reply to the sender and acknowledge receipt.
    client.send_message(
        {
            "from": hotline_number,
            "to": from_number,
            "text": f"Thanks for contacting the {get_hotline_description()}. Someone should follow-up shortly. Note: they may follow up from a different number.",
        }
    )

    return web.Response(status=204)


@routes.get("/recordings/")
async def proxy_recording(request):
    """Endpoint for proxying Nexmo recording downloads.

    This can be used in Zapier to upload recordings to Google Drive

    api_key and api_secret must be provided as GET parameters so we can authenticate
    this request.
    """
    recording_url = request.rel_url.query["recording_url"]

    api_key = os.environ.get("NEXMO_API_KEY")
    api_secret = os.environ.get("NEXMO_API_SECRET")

    incoming_api_key = request.rel_url.query["api_key"]
    incoming_api_secret = request.rel_url.query["api_secret"]

    if api_key != incoming_api_key or api_secret != incoming_api_secret:
        return web.Response(status=401)

    client = get_nexmo_client()
    return web.Response(
        body=client.get_recording(recording_url), content_type="audio/mpeg"
    )


if __name__ == "__main__":  # pragma: no cover
    app = web.Application()
    app.router.add_routes(routes)

    port = os.environ.get("PORT")

    if port is not None:
        port = int(port)

    web.run_app(app, port=port)
