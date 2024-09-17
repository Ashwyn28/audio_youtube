from fastapi import HTTPException
from utils import parse_query_response
import requests
import os

class YoutubeAPI:
    BASE_URL = "https://www.googleapis.com/youtube/v3/search"

    def __init__(self):
        self.API_KEY = os.getenv('API_KEY')    
        self.params = {
            "key": self.API_KEY
        }

    async def search_query(self, q):
        params = {
            "part": "snippet",
            "q": q,    
            "type": "video",
            "maxResults": 1,
            **self.params,
        }

        response = requests.get(self.BASE_URL, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            error_detail = response.text 
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Failed to fetch data from YouTube API {error_detail}",
            )

    
    async def search_channel_latest(self, channel_id):
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "order": "date",
            "maxResults": 3,
            **self.params
        }
        response = requests.get(self.BASE_URL, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(
                status_code=response.status_code,
                detail="Failed to fetch data from YouTube API",
            )
        

    async def search_channels_latest(self, channel_ids):
        data = []
        for channel_id in channel_ids:
            res = await self.search_channel_latest(channel_id)
            channel_data = parse_query_response(res)
            for video in channel_data:
                data.append(video)

        return data

        