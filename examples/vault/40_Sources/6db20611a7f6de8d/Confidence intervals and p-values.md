# Confidence intervals and p-values

## Confidence intervals

A 95 percent confidence interval comes from a procedure that, over many repeated samples, captures the true value 95 percent of the time. It does not mean that there is a 95 percent probability that the true value lies inside one particular interval that has already been computed; that reading belongs to Bayesian credible intervals. The width of the interval shows how precise the estimate is: smaller samples and noisier data give wider intervals.

## P-values

A p-value is the probability of getting a result at least as extreme as the one observed, assuming that the null hypothesis (usually "there is no effect") is true. A small p-value means the data would be surprising if there were no effect. It is not the probability that the null hypothesis is true, and it is not the probability that the result happened by chance. The common threshold of 0.05 is a convention, not a law of nature.

## Statistical and practical significance

With a large enough sample, even a tiny effect gives a small p-value. A study of 100,000 students might find that a new study app raises exam scores by 0.2 points out of 100, with p < 0.001. That effect is statistically significant but too small to matter. Reporting the size of the effect with its confidence interval tells readers whether the effect is large enough to care about; a p-value alone does not.

## Multiple comparisons

If a researcher tests 20 independent hypotheses that are all false, using a threshold of 0.05, the chance of at least one "significant" result is about 64 percent. Testing many outcomes, subgroups or analysis choices and reporting only the significant ones produces false discoveries. Remedies include stating the hypothesis before collecting the data (pre-registration), adjusting the threshold for the number of tests (the Bonferroni correction divides 0.05 by the number of tests), and repeating the study on new data.
