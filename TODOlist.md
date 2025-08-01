# Workflow for Technique Implementation and Orchestration

## TODO after the main task is completed

1. In `F:\我的资料\科研\2025 Hongkong\DeepCode\ui\components.py`, remove the exemplary code in `tech_input_component`.

## Workflow Steps

1. Get `user_query` and `file`.

2. In `orchestrate_research_analysis_agent` $\rightarrow$ `run_research_analyzer`, hget the type of `file` and `user_query`：
    ```python
    {
        "input_type": "text|file|directory|url",
        "user_query": ...,
        "path": "detected path or URL or null",
        "document_info": {
            "title": "a brief description of the document",
        },
        "requirements": [
            "exact requirement from additional_input"
        ]
    }
    ```

---

3. According to the content (**here, URL content should be `HTML` instead of `pdf`**) above, we download/interpret the file and convert to `1.md`.

4. Search Aumentation

    - Given the `user_query` and `1.md`, extract the relevant URLs;
    - \* Download the content (`HTML`) of these URLs, convert to `.md`, and save them into `temp.md`;
    - Segment the contents in `temp.md` into several snippets;
    - Use i) RAG ii) LLM iii) other MCP tools to retrieve the relevant snippets from `temp.md` based on the `user_query`;
    - Concatenate the retrieved snippets and save them into `1.md`;
    - From those snippets, extract the relevant URLs again,
    - Go to \*

5. According to the content of `1.md`, generate the `init_plan.txt`

    - Seperate `user_query` into `tasks`. For each task, entail the relavant index of components in `1.md`. For example:

    `user_query` = "I want to make an MCP server that enable the intelligent Online Search Tool provided by OpenAI, which uses the web search API to retrieve information from the internet. The server should be able to handle user queries and return relevant search results. It should also support advanced features like filtering, sorting, and pagination of search results. The implementation should be scalable and efficient, with proper error handling and logging."
    
    ```python
        tasks = [
            {
                "task": "Initialize the MCP server",
                "components": [1, 3, 5]
            },
            {
                "task": "Enable the intelligent Online Search Tool",
                "components": [2, 4, 6]
            },
            {
                "task": "Implement web search API integration",
                "components": [7, 8, 9]
            },
            {
                "task": "Handle user queries and return search results",
                "components": [10, 11, 12]
            },
            {
                "task": "Support advanced features like filtering, sorting, and pagination",
                "components": [13, 14, 15]
            },
            ...
        ]
    ```

    - Based on those `tasks`, generate the `init_plan.txt`.

---


