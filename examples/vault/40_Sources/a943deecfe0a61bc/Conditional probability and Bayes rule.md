# Conditional probability and Bayes' rule

## Conditional probability

P(A | B) is the probability of A given that B is true. It is computed by looking only at the cases where B holds: P(A | B) = P(A and B) / P(B). In general P(A | B) is not equal to P(B | A). The probability that a person with a cough has the flu is not the same as the probability that a person with the flu has a cough. Mixing up the two is called confusion of the inverse; in a courtroom it is known as the prosecutor's fallacy.

## Bayes' rule

Bayes' rule turns one conditional probability into the other:

P(H | E) = P(E | H) × P(H) / P(E)

H is a hypothesis and E is the evidence. P(H) is the prior, the probability of the hypothesis before seeing the evidence. P(E | H) is the likelihood of the evidence if the hypothesis is true. P(H | E) is the posterior, the probability after seeing the evidence. The denominator can be computed as P(E) = P(E | H) × P(H) + P(E | not H) × P(not H). The rule says how much a piece of evidence should change a belief: evidence that is much more likely when H is true than when H is false raises the probability of H a lot; evidence that is about equally likely either way hardly changes it.

## A screening test

A disease affects 1 in 100 people. A test detects it in 90 percent of the people who have it (its sensitivity) and gives a false positive in 9 percent of the people who do not. Out of 1,000 people, 10 have the disease and 9 of them test positive. Of the 990 people without the disease, about 89 test positive. So of roughly 98 positive results only 9 are true: a positive result means about a 9 percent chance of having the disease, not 90 percent.

The share of positive results that are true is called the positive predictive value. It depends on the base rate, the share of people who have the condition before testing. The same test used in a group where half the people are sick has a positive predictive value of about 91 percent.

## Base rate neglect

People asked about the screening example usually answer close to 90 percent. They focus on the accuracy of the test and ignore how rare the disease is. This error is called base rate neglect. Writing the problem as counts out of 1,000 people, as above, makes the correct answer much easier to see than writing it with percentages.
