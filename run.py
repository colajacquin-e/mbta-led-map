"""Simple launcher for the MBTA LED Map server."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.mbta_led_map.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
