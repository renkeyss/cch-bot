# -*-123 coding: utf-8 -*-
import os
import sys
import aiohttp
import logging
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv
from openai import OpenAIError, OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Retrieve environment variables
openai_api_key=.env("OPENAI_API_KEY")
assistant_id=.env("ASSISTANT_ID")
channel_secret=.env('ChannelSecret')
channel_access_token=.env('ChannelAccessToken')

if not openai_key or not assistant_id:
    logger.error('OpenAI API keys are missing.')
    sys.exit(1)

if not channel_secret or not channel_access_token:
    logger.error('LINE channel secret or token is missing.')
    sys.exit(1)

app = FastAPI()
session = aiohttp.ClientSession()

# Setup LINE bot API
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(channel_access_token, async_http_client)
parser = WebhookParser(channel_secret)

user_message_counts = {}
USER_DAILY_LIMIT = 10

introduction_message = (
    "我是 彰化基督教醫院 內分泌暨新陳代謝科 小助理，..."
    "但基本上我是由 OPENAI 大型語言模型訓練..."
)

def reset_user_count(user_id):
    user_message_counts[user_id] = {
        'count': 0,
        'reset_time': datetime.now() + timedelta(days=1)
    }

async def call_openai_assistant_api(user_message):
    logger.info(f"Calling OpenAI with message: {user_message}")

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)

        thread = client.beta.threads.create(
            messages=[{"role": "user", "content": f"{user_message}。請用中文回答。"}]
        )
        run = client.beta.threads.runs.create_and_poll(
            thread_id=thread.id, assistant_id=assistant_id
        )

        messages = list(client.beta.threads.messages.list(thread_id=thread.id, run_id=run.id))
        message_content = messages[0].content[0].text
        
        # Process annotations if any
        annotations = message_content.annotations
        citations = []
        for index, annotation in enumerate(annotations):
            message_content.value = message_content.value.replace(annotation.text, f"[{index}]")
            if file_citation := getattr(annotation, "file_citation", None):
                cited_file = client.files.retrieve(file_citation.file_id)
                citations.append(f"[{index}] {cited_file.filename}")

        return message_content.value

    except OpenAIError as e:
        logger.error(f"OpenAI API error: {e}")
        return "抱歉，我無法處理您的請求，請稍後再試。"

    except Exception as e:
        logger.error(f"Unknown error occurred when calling OpenAI Assistant: {e}")
        return "系統出現錯誤，請稍後再試。"

@app.post("/callback")
async def handle_callback(request: Request):
    signature = request.headers.get('X-Line-Signature', None)
    if not signature:
        logger.error("Missing X-Line-Signature header")
        raise HTTPException(status_code=400, detail="Signature missing")

    body = await request.body()
    body = body.decode()
    logger.info(f"Request body: {body}")

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature error")
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if not isinstance(event, MessageEvent) or not isinstance(event.message, TextMessage):
            continue

        user_id = event.source.user_id
        user_message = event.message.text

        logger.info(f"Received message from user {user_id}: {user_message}")

        if user_id not in user_message_counts:
            reset_user_count(user_id)
        elif datetime.now() >= user_message_counts[user_id]['reset_time']:
            reset_user_count(user_id)

        if user_message_counts[user_id]['count'] >= USER_DAILY_LIMIT:
            logger.info(f"User {user_id} exceeded daily limit")
            await line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="您好：您的問題...")
            )
            continue

        if "介紹" in user_message or "你是誰" in user_message:
            logger.info(f"Handling introduction request for user {user_id}")
            await line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=introduction_message)
            )
            continue

        try:
            result_text = await call_openai_assistant_api(user_message)
        except Exception as e:
            logger.error(f"Error processing user {user_id} message: {e}")
            result_text = "處理訊息時發生錯誤，請稍後重試。"

        user_message_counts[user_id]['count'] += 1

        logger.info(f"Replying to user {user_id} with message: {result_text}")
        await line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=result_text)
        )

    return 'OK'
