import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

@pytest.mark.asyncio
async def test_channels_latest():
    query_data = [
        'UCBJycsmduvYEL83R_U4JriQ',
        'UCEcrRXW3oEYfUctetZTAWLw'
    ]

    response = client.post("/channels/latest", json=query_data)

    assert response.status_code == 200

@pytest.mark.asyncio
async def test_search():
    query = "test query"

    response = client.get(f"/search?query={query}")

    assert response.status_code == 200

@pytest.mark.asyncio
async def test_channel_latest():
    channel_id = "UCBJycsmduvYEL83R_U4JriQ"

    response = client.get(f"/channel/latest?channel_id={channel_id}")

    assert response.status_code == 200