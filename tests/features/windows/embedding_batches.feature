Feature: Submit missing window embeddings
  Submission records survive source replacement and prevent duplicate outstanding requests.

  Background:
    Given a book has primary and translated windows ready for embedding

  Scenario: Submit missing vectors once
    Given its primary embedding already has a vector
    When I submit its embeddings twice
    Then only its missing translated vector is requested once

  Scenario: Submit multiple model and dimension profiles
    Given its window has additional model and dimension profiles
    When I submit its embeddings
    Then each model receives a keyed file preserving its request dimensions

  Scenario: Retry a failed upload
    Given the batch input upload fails
    When I submit its embeddings
    Then its failed upload claims are released
    When the provider recovers and I submit its embeddings
    Then a new batch is submitted

  Scenario: Retry a rejected submission
    Given the provider rejects batch creation
    When I submit its embeddings
    Then its rejected submission claims are released
    When the provider recovers and I submit its embeddings
    Then a new batch is submitted

  Scenario: Retain an uncertain submission
    Given the batch creation response is lost
    When I submit its embeddings twice
    Then its uncertain submission remains claimed without another provider request

  Scenario: Rebuild windows during submission
    Given its windows are rebuilt while the batch is submitted
    When I submit its embeddings
    Then the submitted identities survive and the replacement vectors stay empty

  Scenario: Reject inconsistent stored input
    Given its stored input hash is stale
    When I submit its embeddings
    Then no batch is stored or sent

  Scenario Outline: Collect keyed results safely and repeatedly
    Given its submitted batch returns results
    And the collection case is <case>
    When maintenance collects twice
    Then the batch is <state> with <vectors> vectors and <failures> collection failures

    Examples:
      | case          | state                 | vectors | failures |
      | success       | completed             | 2       | 0        |
      | request-error | completed_with_errors | 1       | 0        |
      | changed       | completed             | 1       | 0        |
      | deleted       | completed             | 0       | 0        |
      | duplicate     | submitted             | 0       | 1        |
      | missing       | submitted             | 0       | 1        |
      | unknown       | submitted             | 0       | 1        |
      | dimensions    | submitted             | 0       | 1        |
      | nonfinite     | submitted             | 0       | 1        |
      | boolean       | submitted             | 0       | 1        |
      | zero          | submitted             | 0       | 1        |
      | empty         | submitted             | 0       | 1        |
      | malformed     | submitted             | 0       | 1        |
      | running       | submitted             | 0       | 0        |
      | failed        | failed                | 0       | 0        |
      | cancelled     | failed                | 0       | 0        |
      | expired       | failed                | 0       | 0        |

  Scenario Outline: Reconcile interrupted submissions after the grace period
    Given an interrupted submission is <age> and its provider listing is <listing>
    When maintenance collects twice
    Then its reconciliation state is <state> and claims are <active>

    Examples:
      | age | listing | state      | active |
      | old | matched | submitted  | yes    |
      | old | absent  | failed     | no     |
      | old | failure | submitting | yes    |
      | new | absent  | submitting | yes    |
