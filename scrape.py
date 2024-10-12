import requests
from bs4 import BeautifulSoup
import json

# URL of the WordFinder Wordle answers page
url = "https://wordfinder.yourdictionary.com/wordle/answers/"

# Send a request to the webpage
response = requests.get(url)

# Parse the HTML content using BeautifulSoup
soup = BeautifulSoup(response.content, 'html.parser')

# Initialize an empty list to store word data
word_list = []

# Find the relevant section for Wordle answers and loop through the rows
for row in soup.select('table tr'):
    columns = row.find_all('td')
    
    if len(columns) > 2:
        date = columns[0].text.strip()
        puzzle_number = columns[1].text.strip()
        word = columns[2].text.strip().lower()
        
        # Append the extracted data to the list
        word_list.append({
            'date': date,
            'puzzle_number': puzzle_number,
            'word': word
        })

# Save the word list to a JSON file
with open('wordle_word_list.json', 'w') as json_file:
    json.dump(word_list, json_file, indent=4)

print(f"Scraped {len(word_list)} Wordle words successfully.")
