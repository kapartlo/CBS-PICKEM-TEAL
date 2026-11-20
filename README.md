# CBS Pick'em

## Run the recommendation script locally

Create `oddsapikey.env` next to `PICKEM_RECOMMEND.py` with your Odds API key:

```text
ODDS_API_KEY=your-api-key
```

The key file is ignored by Git. Once created, run the script normally:

```powershell
python .\PICKEM_RECOMMEND.py
```

In Google Colab, the script can continue to read `ODDS_API_KEY` from Colab Secrets.

## Configure the Streamlit workbook

The Streamlit app uses the default Google Drive spreadsheet ID in its source unless `picks_spreadsheet_id` is set in Streamlit Secrets. Set that value to the ID from the current spreadsheet's Drive URL (`/d/<ID>/edit`) to use a replacement file.
