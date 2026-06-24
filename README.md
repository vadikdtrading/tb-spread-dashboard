from datetime import date, timedelta
from data_sources import fetch_energycharts
from spreads import top_bottom_spread

if __name__ == "__main__":
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=2)
    df = fetch_energycharts(["DE-LU"], start, end)
    print(df.head())
    print(top_bottom_spread(df, n=2).tail())
