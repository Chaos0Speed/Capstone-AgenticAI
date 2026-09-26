import os
from google import genai

def study_buddy():
    # Retrieve the API key from the environment variables if set, for security and reusability purposes
    # or use directly
    # KEY = "Place your API KEY here"
    KEY = os.environ.get("GEMINI_API_KEY")
    if not KEY:
        print("Error: GEMINI_API_KEY environment variable is not set.")
        exit(1)

    # Initialize the Google Gen AI client
    client = genai.Client(api_key=KEY)

    # Get topic input from user
    topic = ""
    while not topic:
        topic = (input("Enter the topic you want to the explanation for : ")).strip()

    print(f"\nSending {topic} to the study bot.\nPls Wait........",flush=True)
    try:
        # Sending the API request to gemini client
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Explain {topic} to a beginner,easy to understand format,covering the core concept.",
            config=genai.types.GenerateContentConfig(
                system_instruction="You are a senior professor in a prestigious university and are helping a student gain proper introduction into their topic of interest.Answer precisely in precisely 100 words.", #System Instruction
                max_output_tokens=250,      #Token limit to hardcord the 100 word limit [slightly higher to prevent abrupt breaks]
                thinking_config={"thinking_budget":0} #Found after debugging the since gemini flash is a resoning model the thinking part was interfering with the token limit, so disabled that
            )
        )

        print("-------- Explanation --------\n")
        print(response.text,"\n")
        print("-----------------------------")

    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    print("-------- Welcome to the STUDY BUDDY --------\n")
    study_buddy()
