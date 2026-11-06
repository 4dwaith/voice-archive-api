Feature: Answer questions from a complete book
  Answers use the selected book's published text and provide passages that can
  be followed in the reader. Unavailable or changed evidence never becomes an answer.

  Background:
    Given my library contains a published book about a journey
    And the answer service is available

  Scenario: Ask a question and receive supporting passages
    When I ask why the traveller returned home
    Then the answer explains the return with its supporting passage
    And the answer service receives the question and the complete ordered book

  Scenario: The book does not establish an answer
    Given the answer service finds no evidence for the question
    When I ask why the traveller returned home
    Then I am told that the book does not establish an answer

  Scenario Outline: Request a reasoning effort for the question
    Given I want <effort> reasoning effort
    When I ask why the traveller returned home
    Then the answer explains the return with its supporting passage
    And the answer service uses the requested reasoning effort

    Examples:
      | effort |
      | low    |
      | medium |
      | high   |

  Scenario Outline: An unusable answer is not delivered
    Given the answer service encounters <problem>
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable

    Examples:
      | problem                       |
      | a network failure             |
      | a timeout                     |
      | a provider rejection          |
      | malformed output              |
      | a blocked response            |
      | a truncated response          |
      | an unknown passage            |
      | a mismatched passage reference |
      | a passage from another book   |
      | repeated passages             |
      | an answer without support     |
      | an empty answer               |
      | an unexplained passage        |
      | missing model limits          |
      | missing token count           |

  Scenario Outline: A complete book must fit before it is submitted for answering
    Given the complete request exceeds the <limit> input limit
    When I ask why the traveller returned home
    Then I am told the complete book is too large without submitting a partial book

    Examples:
      | limit      |
      | provider   |
      | configured |

  Scenario Outline: Missing source is explicit
    Given the selected book is <state>
    When I ask why the traveller returned home
    Then I am told the selected source is <availability>

    Examples:
      | state                  | availability |
      | not imported           | unavailable  |
      | removed                | missing      |
      | published without text | unavailable  |

  Scenario Outline: A slow answer does not prevent source changes
    Given the book is <change> while the answer is being prepared
    When I ask why the traveller returned home
    Then the source change completes and I am told to ask again

    Examples:
      | change   |
      | purged   |
      | replaced |
      | deleted  |

  Scenario: Answering needs server configuration
    Given the answer service has no credential
    When I ask why the traveller returned home
    Then I am told that an answer is temporarily unavailable
