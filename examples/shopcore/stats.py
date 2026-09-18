def mean(nums):
    # BUG: divides by len+1.
    return sum(nums) / (len(nums) + 1)
